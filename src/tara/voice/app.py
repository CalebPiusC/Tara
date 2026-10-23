"""LiveTaraApp: composition root for the Gemini Live path (the primary voice path).

The older cascaded path (`tara.legacy.app.TaraApp`: STT -> router -> LLM -> TTS) is kept as
optional code and is NOT started here. No local model is loaded or downloaded.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..behaviour.controller import BehaviourController
from ..behaviour.expressions import ExpressionEmitter, ExpressionSink, NullExpressionSink
from ..behaviour.meeting_kind import MeetingKindTracker
from ..behaviour.profiles import effective_profile, parse_profile
from ..control.commands import CommandKind
from ..control.policy import DEFAULT_ENABLED
from ..control.state import ControlState
from ..core.config import INTERVIEW_MODES, MeetingConfig, Role, ToolSpec
from ..core.contracts import MeetingAdapter
from ..core.errors import ConfigError
from ..core.events import EventBus
from ..core.metrics import Metrics
from ..core.settings import RuntimeSettings, validate
from ..floor.classify import IntentClassifier, RuleIntentClassifier
from ..floor.participation import Thresholds, min_confidence
from ..meetings.types import RULES, parse_type
from ..memory import (
    KnowledgeService,
    MeetingSession,
    MemoryScope,
    MemoryService,
    MemoryWriteGate,
    PostgresKnowledgeStore,
    PostgresMemoryStore,
    knowledge_tool_grants,
    meeting_slug,
    memory_tool_grants,
)
from ..tools.executor import ToolDef, ToolExecutor
from ..tools.utility import Orchestration
from ..tools.utility.http import StdlibHttpClient
from ..work import WorkServices, text_llm_from_settings
from .engine import LiveParams, LiveVoiceEngine
from .events import ToolDecl, VoiceProvider, VoiceSessionConfig
from .gemini_live import GeminiLiveProvider
from .instructions import live_system_instruction
from .memory import InMemoryMeetingMemory, MeetingMemory, Scope, WorkingMemory, record_note_tool
from .participation import LiveParticipation
from .playback import PlaybackController, PlaybackResult
from .registry import SessionRegistry

DEFAULT_THRESHOLDS = Thresholds(min_intent_confidence=0.5, proactive_usefulness=0.7,
                                proactive_min_pause_ms=1500, default_attribution=0.8)
_TYPES = {str: "STRING", int: "INTEGER", float: "NUMBER", bool: "BOOLEAN"}
# A thinking pause in an interview is not the end of an answer. This is the provider's own VAD silence window; an explicit
# TARA_LIVE_SILENCE_MS still wins. It does not replace the application's three-minute question deadline.
INTERVIEW_SILENCE_MS = 1200


def tool_declarations(executor: ToolExecutor | None) -> tuple[ToolDecl, ...]:
    """Only tools that are granted AND registered are ever shown to the model."""
    if executor is None:
        return ()
    out = []
    for d in executor.catalog():
        out.append(ToolDecl(d["name"], d["description"], d.get("schema") or {"type": "OBJECT", "properties": {}}))
    return tuple(out)


def _add_tool_defs(tools: ToolExecutor | None, definitions: tuple[ToolDef, ...]) -> ToolExecutor | None:
    """Register code-owned orchestration tools; the model never supplies a grant."""
    if not definitions:
        return tools
    tools = tools or ToolExecutor({}, ())
    existing = {d["name"] for d in tools.describe()}
    for definition in definitions:
        if definition.name in existing:
            raise ConfigError(f"tool {definition.name!r} is already registered")
        tools.register_and_grant(definition, ToolSpec(definition.name, definition.permission or "orchestration",
                                                      requires_human_approval=False))
        existing.add(definition.name)
    return tools


class LiveTaraApp:
    def __init__(self, settings: RuntimeSettings, cfg: MeetingConfig, roles: dict[str, Role], names: dict[str, str],
                 adapter: MeetingAdapter, *, session_id: str, scope: Scope, provider: VoiceProvider | None = None,
                 tools: ToolExecutor | None = None, classifier: IntentClassifier | None = None,
                 registry: SessionRegistry | None = None, bus: EventBus | None = None,
                 meeting_memory: MeetingMemory | None = None, thresholds: Thresholds = DEFAULT_THRESHOLDS,
                 expression_sink: ExpressionSink | None = None, memory_service: MemoryService | None = None,
                 knowledge_service: KnowledgeService | None = None,
                 user_id: str = "local-user", orchestration: Orchestration | None = None,
                 interview_code: str = "", defer_opening: bool = False):
        errors = [f"{i.field}: {i.message}" for i in validate(settings) if i.severity == "error"]
        if not settings.live.enabled:
            errors.append("TARA_LIVE_ENABLED: Live voice is not enabled")
        if errors:
            raise ConfigError("invalid settings: " + "; ".join(errors))
        lv = settings.live
        pr = settings.persona
        # profile: the meeting's request wins over the runtime default; interviews are clamped to professional
        self.profile = effective_profile(parse_profile(cfg.behaviour_profile or pr.profile), cfg)
        kind_override = ""
        if settings.work.enabled:
            try:
                kind_override = RULES[parse_type(settings.work.meeting_type)].kind
            except ValueError:
                kind_override = ""
        self.behaviour = BehaviourController(self.profile, cfg.mode, guidance_min_interval_s=pr.guidance_min_interval_s,
                                             kinds=MeetingKindTracker(kind_override, cfg.mode))
        self.expressions = ExpressionEmitter(expression_sink or NullExpressionSink(), session_id)
        self.settings, self.cfg, self.metrics = settings, cfg, Metrics()
        self.control, self.memory = ControlState(), meeting_memory or InMemoryMeetingMemory()
        self.memory_service = memory_service
        self._owns_memory = False
        if self.memory_service is None and settings.memory.enabled:
            store = PostgresMemoryStore(settings.memory.database_url, schema=settings.memory.schema or None,
                                        timeout_s=settings.memory.recall_timeout_s)
            self.memory_service = MemoryService(
                store, metrics=self.metrics, write_timeout_s=settings.memory.write_timeout_s,
                recall_timeout_s=settings.memory.recall_timeout_s)
            self._owns_memory = True
        self.knowledge_service = knowledge_service
        self._owns_knowledge = False
        if self.knowledge_service is None and settings.memory.enabled and settings.memory.knowledge_enabled:
            document_store = PostgresKnowledgeStore(
                settings.memory.database_url, schema=settings.memory.schema or None,
                timeout_s=settings.memory.knowledge_timeout_s)
            self.knowledge_service = KnowledgeService(
                document_store, metrics=self.metrics, search_timeout_s=settings.memory.knowledge_timeout_s,
                write_timeout_s=settings.memory.write_timeout_s,
                default_limit=settings.memory.knowledge_search_limit,
                max_context_chars=settings.memory.knowledge_context_chars)
            self._owns_knowledge = True
        self.memory_scope = (MemoryScope(scope.tenant_id, user_id, meeting_id=meeting_slug(scope.meeting_id))
                             if (self.memory_service is not None or self.knowledge_service is not None) else None)
        self.memory_gate = MemoryWriteGate() if self.memory_service is not None else None
        self.session_id = session_id
        self.interview_code = interview_code
        self.defer_opening = defer_opening          # the opening waits for `say_pending_opening` (the candidate has not joined yet)
        self._pending_opening = ""
        self.last_finalize: dict[str, Any] = {}
        self._catch_up: asyncio.Task[int] | None = None
        self._opening: asyncio.Task[None] | None = None
        self.meeting: MeetingSession | None = None            # set at start(): which numbered meeting this run is
        # Orchestration is a code-owned set of read-only tools. The model can choose
        # among them, but cannot grant one, widen a scope, or speak through them.
        self.orchestration = orchestration
        if self.orchestration is None and settings.tools.enabled:
            self.orchestration = Orchestration(
                settings.tools, session_id=session_id, tenant_id=scope.tenant_id, meeting_id=scope.meeting_id,
                metrics=self.metrics)
        if self.orchestration is not None:
            tools = _add_tool_defs(tools, self.orchestration.tool_defs)
        # Notes are a granted, code-owned tool. The spoken notes mode authorizes each write; the
        # handler re-checks it, and the model can never choose the memory scope.
        notes_enabled = CommandKind.NOTES_ON in (set(map(CommandKind, cfg.allowed_commands)) if cfg.allowed_commands
                                                  else DEFAULT_ENABLED)
        if notes_enabled and not (tools is not None and any(d["name"] == "record_note" for d in tools.describe())):
            tools = tools or ToolExecutor({}, ())
            tools.register_and_grant(
                record_note_tool(self.memory, scope, lambda: self.control.notes_active, source=session_id),
                ToolSpec("record_note", "notes", requires_human_approval=False))
        if self.memory_service is not None and self.memory_scope is not None and self.memory_gate is not None:
            tools = tools or ToolExecutor({}, ())
            existing = {d["name"] for d in tools.describe()}
            memory_tools = memory_tool_grants(self.memory_service, self.memory_scope, self.memory_gate, session_id)
            for definition, grant in memory_tools:
                if definition.name not in existing:
                    tools.register_and_grant(definition, grant)
        if self.knowledge_service is not None and self.memory_scope is not None:
            tools = tools or ToolExecutor({}, ())
            existing = {d["name"] for d in tools.describe()}
            document_tools = knowledge_tool_grants(
                self.knowledge_service, self.memory_scope, limit=settings.memory.knowledge_search_limit,
                max_chars=settings.memory.knowledge_context_chars, timeout_s=settings.memory.knowledge_timeout_s)
            for definition, grant in document_tools:
                if definition.name not in existing:
                    tools.register_and_grant(definition, grant)
        # Secretary, interviewing and documents (off by default). Code-owned tools; Flash writes text, our code renders files.
        self.work: WorkServices | None = None
        if settings.work.enabled:
            from ..hiring.github import GitHubReader
            from ..tools.utility.fetch import SafeFetcher
            fetcher = (self.orchestration.fetcher if self.orchestration is not None and self.orchestration.fetcher is not None
                       else SafeFetcher(user_agent=settings.tools.user_agent, timeout_s=settings.tools.http_timeout_s))
            github = GitHubReader(StdlibHttpClient(user_agent=settings.tools.user_agent), settings.work.github_token,
                                  timeout_s=settings.tools.http_timeout_s, max_repos=settings.work.github_max_repos)
            self.work = WorkServices(
                settings.work, session_id=session_id, scope=scope, cfg=cfg, roles=roles, names=names,
                meeting_memory=self.memory, working=WorkingMemory(), control=self.control,
                min_conf=min_confidence(cfg, thresholds),
                llm=text_llm_from_settings(settings.gemini.api_key, settings.gemini.model, settings.limits.cloud_timeout_s),
                fetcher=fetcher, github=github, memory_service=self.memory_service, memory_scope=self.memory_scope,
                metrics=self.metrics, send_note=lambda note: self.engine.queue_note(note),
                candidate_speakers=frozenset(pid for pid, r in roles.items() if r is Role.CANDIDATE),
                memory_gate=self.memory_gate, speak=lambda text: self.engine.prompt(text),
                on_kind=lambda kind: self.behaviour.kinds.set_override(kind),
                recorder_llm=(text_llm_from_settings(settings.gemini.api_key, settings.work.recorder_model,
                                                     settings.work.digest_timeout_s)
                              if settings.work.recorder_model else None))
            tools = _add_tool_defs(tools, self.work.tool_defs)
        self.provider: VoiceProvider = provider or GeminiLiveProvider(
            settings.gemini.api_key, lv.model, lv.connect_timeout_s, lv.api_version or None)
        instruction = live_system_instruction(cfg, names, roles, self.profile, pr.vocal_reactions,
                                              memory_enabled=self.memory_service is not None,
                                              knowledge_enabled=self.knowledge_service is not None)
        if self.orchestration is not None:
            instruction = f"{instruction}\n{self.orchestration.instructions()}"
        if self.work is not None:
            instruction = f"{instruction}\n{self.work.instructions()}"
        session_cfg = VoiceSessionConfig(
            system_instruction=instruction,
            voice_name=lv.voice_name,
            tools=tool_declarations(tools), input_transcription=lv.input_transcription,
            output_transcription=lv.output_transcription, resumption=lv.session_resumption,
            context_compression=lv.context_compression,
            silence_duration_ms=(lv.silence_duration_ms or INTERVIEW_SILENCE_MS) if cfg.mode in INTERVIEW_MODES
            else (lv.silence_duration_ms or None),
            proactive_audio=lv.proactive_audio, tool_behavior=lv.tool_behavior)
        self.playback = PlaybackController(adapter, self.metrics, lv.playback_queue_chunks)
        self.engine = LiveVoiceEngine(
            session_id=session_id, scope=scope, cfg=cfg, roles=roles, names=names, provider=self.provider,
            session_config=session_cfg, adapter=adapter,
            participation=LiveParticipation(cfg, roles, classifier or RuleIntentClassifier(), thresholds),
            registry=registry or SessionRegistry(lv.max_concurrent_sessions), playback=self.playback, tools=tools,
            working=self.work.working if self.work is not None else WorkingMemory(), meeting_memory=self.memory,
            control=self.control, bus=bus,
            transcript_observers=(self.work.on_transcript,) if self.work is not None else (),
            turn_observers=(self.work.on_turn,) if self.work is not None else (),
            metrics=self.metrics, behaviour=self.behaviour, expressions=self.expressions,
            memory_gate=self.memory_gate,
            params=LiveParams(floor_wait_ms=pr.floor_wait_ms, guidance_injection=pr.guidance_injection, hold_ms=lv.hold_ms,
                              uplink_queue=lv.uplink_queue_chunks, reconnect_attempts=lv.reconnect_attempts,
                              reconnect_backoff_s=lv.reconnect_backoff_s, local_barge_in=lv.local_barge_in,
                              hybrid_end_of_speech=lv.hybrid_end_of_speech,
                              max_tool_calls_per_turn=settings.tools.max_calls_per_turn))
        if self.work is not None:
            self._observe_playback_for_interviews()
        if self.orchestration is not None and self.orchestration.tasks is not None:
            self.orchestration.tasks.on_done = self._research_finished
        self._announcements: set[asyncio.Task[None]] = set()
        self._memory_tasks: set[asyncio.Task[int]] = set()

    def _memory_task_done(self, task: asyncio.Task[int]) -> None:
        """A background consolidation must never surface as an unhandled task exception at shutdown."""
        self._memory_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.metrics.inc("memory_consolidate_failures")

    def _research_finished(self, status: str, query: str, error: str) -> None:
        """A background research task ended: say so, so nobody has to ask "is it done?". The result itself is read out only on request."""
        text = (f"Tell the participants, in one short sentence, that the research on \"{query[:80]}\" is finished and you can tell them "
                "what you found if they want." if status == "completed" else
                f"Tell the participants, in one short sentence, that the research on \"{query[:80]}\" did not work out"
                + (" because it ran out of time" if "timeout" in error else "") + ", and offer to try again.")
        t = asyncio.get_running_loop().create_task(self._announce(text), name="research-announce")
        self._announcements.add(t)
        t.add_done_callback(self._announcements.discard)

    async def _announce(self, text: str) -> None:
        try:
            if self.engine.status == "active":
                await self.engine.prompt(text)
                self.metrics.inc("background_announced")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a missed announcement is not worth an error
            self.metrics.inc("background_announce_failed")

    def _observe_playback_for_interviews(self) -> None:
        """Let the interview clock start when the question audio really finishes.

        The engine owns playback completion for expressions; this wraps that existing callback instead of creating a
        second playback notification path. A failed or cancelled playback is reported too, so the interview falls back
        to the tool-return deadline rather than starting the clock on an interrupted question.
        """
        engine_finished = self.playback.on_finished
        if engine_finished is None:
            return

        def finished(result: PlaybackResult) -> None:
            engine_finished(result)
            if self.work is not None:
                self.work.on_playback_finished(result.response_id, completed=result.completed, cancelled=result.cancelled)

        self.playback.on_finished = finished

    async def start(self) -> None:
        context = ""
        if self.memory_service is not None and self.memory_scope is not None:
            svc, mscope = self.memory_service, self.memory_scope
            try:
                self.meeting = await svc.start_session(mscope, self.session_id)        # this run becomes meeting N
                recovered = await svc.recover(mscope, self.session_id)                 # close what a dead run left open
                await asyncio.wait_for(svc.store.purge_expired(mscope), self.settings.memory.write_timeout_s)
                if recovered:
                    self.metrics.inc("memory_sessions_recovered", len(recovered))
                task = asyncio.create_task(svc.consolidate(mscope), name="memory-consolidate")
                self._memory_tasks.add(task)
                task.add_done_callback(self._memory_task_done)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - memory trouble must not stop the voice session
                self.metrics.inc("memory_session_start_failures")
            if self.meeting is not None and self.work is not None and self.work.recorder is not None:
                previous = [m for m in await svc.sessions(mscope) if m.number != self.meeting.number]
                self._catch_up = asyncio.create_task(self.work.recorder.catch_up(previous))     # never blocks the voice start
            if self.settings.memory.startup_context:
                parts = []
                if self.meeting is not None:
                    parts.append(await svc.meeting_brief(mscope, self.meeting))
                parts.append(await svc.startup_context(mscope, limit=self.settings.memory.search_limit,
                                                       max_chars=self.settings.memory.context_max_chars))
                context = "\n\n".join(p for p in parts if p)
                if context:
                    self.metrics.inc("memory_startup_context_injected")
        await self.engine.start(startup_context=context)
        if self.work is not None and self.interview_code:
            try:
                text = self.work.begin_interview(self.interview_code)
            except Exception as exc:  # noqa: BLE001 - say what is wrong instead of starting a silent, empty interview
                raise ConfigError(f"could not start the interview {self.interview_code!r}: {exc}") from exc
            if self.defer_opening:
                self._pending_opening = text
            else:
                self._opening = asyncio.create_task(self._say_opening(text))
        elif self.work is not None and self.meeting is not None:
            text = await self.work.opening_prompt(self.meeting)
            if text:
                self._opening = asyncio.create_task(self._say_opening(text))

    async def say_pending_opening(self) -> bool:
        """Speak the deferred opening once (for example when the candidate joins a meeting). False if there is none."""
        text, self._pending_opening = self._pending_opening, ""
        if not text:
            return False
        await self._say_opening(text)
        return True

    async def _say_opening(self, text: str) -> None:
        """Tara's unprompted first words for a continuing section. Goes through the normal gate (mute and takeover win)."""
        try:
            # The voice session is already open here. No artificial thinking delay: a 1 s pause made the interviewer feel
            # unresponsive and served no protocol purpose.
            await self.engine.prompt(text)
            if self.work is not None and self.interview_code:
                self.work.mark_welcome_delivered()
            self.metrics.inc("opening_brief_spoken")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a missed greeting is not an error worth stopping for
            self.metrics.inc("opening_brief_failed")

    async def close(self) -> None:
        for t in list(getattr(self, "_announcements", ())):
            t.cancel()
        for t in list(getattr(self, "_memory_tasks", ())):
            t.cancel()
        if self._catch_up is not None and not self._catch_up.done():
            self._catch_up.cancel()
        if self._opening is not None and not self._opening.done():
            self._opening.cancel()
        try:
            await self.engine.close()
        finally:
            try:
                if self.memory_service is not None and self.memory_scope is not None and self.meeting is not None:
                    await self.memory_service.end_session(self.memory_scope, self.session_id)   # cheap, so do it first
                if self.work is not None:
                    self.last_finalize = await self.work.finalize()
                    await self.work.close()
                if self.orchestration is not None:
                    await self.orchestration.close()
            finally:
                if self._owns_memory and self.memory_service is not None:
                    try:
                        await asyncio.wait_for(self.memory_service.close(), self.settings.memory.write_timeout_s)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - shutdown must finish; the failure is counted, never hidden
                        self.metrics.inc("memory_close_failures")
                if self._owns_knowledge and self.knowledge_service is not None:
                    try:
                        await asyncio.wait_for(self.knowledge_service.close(), self.settings.memory.knowledge_timeout_s)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - shutdown must finish; the failure is counted, never hidden
                        self.metrics.inc("knowledge_close_failures")

    def health(self) -> dict[str, Any]:
        e = self.engine
        out = {"status": e.status, "epoch": e.epoch, "takeover": e.takeover_active, "muted": e.muted,
               "playing": self.playback.is_playing, "model": self.settings.live.model, "profile": self.profile.value,
               "key": "set" if self.settings.gemini.api_key else "missing",
               "memory": "enabled" if self.memory_service is not None else "disabled",
               "knowledge": "enabled" if self.knowledge_service is not None else "disabled"}
        if self.orchestration is not None:
            out["orchestration"] = self.orchestration.health()
        if self.work is not None:
            out["work"] = {"auto_memory": self.settings.work.auto_memory, "jobs_active": self.work.jobs.active,
                           "text_model": "configured" if self.work.llm is not None else "none (templates only)"}
        return out
