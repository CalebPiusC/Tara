"""LiveTaraApp: composition root for the Gemini Live path (the primary voice path).

The older cascaded path (`tara.app.TaraApp`: STT -> router -> LLM -> TTS) is kept as
optional code and is NOT started here. No local model is loaded or downloaded.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..behaviour.controller import BehaviourController
from ..behaviour.expressions import ExpressionEmitter, ExpressionSink, NullExpressionSink
from ..behaviour.profiles import effective_profile, parse_profile
from ..classify import IntentClassifier, RuleIntentClassifier
from ..config import MeetingConfig, Role, ToolSpec
from ..contracts import KnowledgeProvider, MeetingAdapter
from ..control.commands import CommandKind
from ..control.policy import DEFAULT_ENABLED
from ..control.state import ControlState
from ..errors import ConfigError
from ..events import EventBus
from ..memory import (
    MemoryScope,
    MemoryService,
    MemoryWriteGate,
    PostgresMemoryStore,
    forget_memory_tool,
    recall_memory_tool,
    remember_memory_tool,
)
from ..metrics import Metrics
from ..participation import Thresholds
from ..settings import RuntimeSettings, validate
from ..tools import ToolDef, ToolExecutor
from .engine import LiveParams, LiveVoiceEngine
from .events import ToolDecl, VoiceProvider, VoiceSessionConfig
from .gemini_live import GeminiLiveProvider
from .instructions import live_system_instruction
from .memory import InMemoryMeetingMemory, MeetingMemory, Scope, WorkingMemory, record_note_tool, scoped_search
from .participation import LiveParticipation
from .playback import PlaybackController
from .registry import SessionRegistry

DEFAULT_THRESHOLDS = Thresholds(min_intent_confidence=0.5, proactive_usefulness=0.7,
                                proactive_min_pause_ms=1500, default_attribution=0.8)
_TYPES = {str: "STRING", int: "INTEGER", float: "NUMBER", bool: "BOOLEAN"}


def tool_declarations(executor: ToolExecutor | None) -> tuple[ToolDecl, ...]:
    """Only tools that are granted AND registered are ever shown to the model."""
    if executor is None:
        return ()
    out = []
    for d in executor.describe():
        props = {k: {"type": _TYPES.get({"str": str, "int": int, "float": float, "bool": bool}.get(t, str), "STRING")}
                 for k, t in d["params"].items()}
        out.append(ToolDecl(d["name"], d["description"], {"type": "OBJECT", "properties": props, "required": d["required"]}))
    return tuple(out)


def knowledge_tool(knowledge: KnowledgeProvider, scope: Scope, timeout_s: float = 5.0) -> ToolDef:
    """`search_knowledge`: the scope is bound here; the model can only supply a query string."""
    search = scoped_search(knowledge, scope)

    async def handler(query: str) -> list[dict[str, str]]:
        return [{"id": p.id, "text": p.text, "source": p.source} for p in await search(query, 3)]
    return ToolDef("search_knowledge", {"query": str}, handler, description="Search approved company knowledge.",
                   timeout_s=timeout_s, permission="knowledge_read", side_effect=False)


class LiveTaraApp:
    def __init__(self, settings: RuntimeSettings, cfg: MeetingConfig, roles: dict[str, Role], names: dict[str, str],
                 adapter: MeetingAdapter, *, session_id: str, scope: Scope, provider: VoiceProvider | None = None,
                 tools: ToolExecutor | None = None, classifier: IntentClassifier | None = None,
                 registry: SessionRegistry | None = None, bus: EventBus | None = None,
                 meeting_memory: MeetingMemory | None = None, thresholds: Thresholds = DEFAULT_THRESHOLDS,
                 expression_sink: ExpressionSink | None = None, memory_service: MemoryService | None = None,
                 user_id: str = "local-user"):
        errors = [f"{i.field}: {i.message}" for i in validate(settings) if i.severity == "error"]
        if not settings.live.enabled:
            errors.append("TARA_LIVE_ENABLED: Live voice is not enabled")
        if errors:
            raise ConfigError("invalid settings: " + "; ".join(errors))
        lv = settings.live
        pr = settings.persona
        # profile: the meeting's request wins over the runtime default; interviews are clamped to professional
        self.profile = effective_profile(parse_profile(cfg.behaviour_profile or pr.profile), cfg)
        self.behaviour = BehaviourController(self.profile, cfg.mode, guidance_min_interval_s=pr.guidance_min_interval_s)
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
        self.memory_scope = MemoryScope(scope.tenant_id, user_id) if self.memory_service is not None else None
        self.memory_gate = MemoryWriteGate() if self.memory_service is not None else None
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
            memory_tools = (
                (remember_memory_tool(self.memory_service, self.memory_scope, self.memory_gate,
                                      source=session_id, session_id=session_id),
                 ToolSpec("remember_memory", "memory", requires_human_approval=False)),
                (recall_memory_tool(self.memory_service, self.memory_scope),
                 ToolSpec("recall_memory", "memory", requires_human_approval=False)),
                (forget_memory_tool(self.memory_service, self.memory_scope, self.memory_gate),
                 ToolSpec("forget_memory", "memory", requires_human_approval=False)),
            )
            for definition, grant in memory_tools:
                if definition.name not in existing:
                    tools.register_and_grant(definition, grant)
        self.provider: VoiceProvider = provider or GeminiLiveProvider(
            settings.gemini.api_key, lv.model, lv.connect_timeout_s, lv.api_version or None)
        session_cfg = VoiceSessionConfig(
            system_instruction=live_system_instruction(cfg, names, roles, self.profile, pr.vocal_reactions,
                                                       memory_enabled=self.memory_service is not None),
            voice_name=lv.voice_name,
            tools=tool_declarations(tools), input_transcription=lv.input_transcription,
            output_transcription=lv.output_transcription, resumption=lv.session_resumption,
            context_compression=lv.context_compression, silence_duration_ms=lv.silence_duration_ms or None,
            proactive_audio=lv.proactive_audio, tool_behavior=lv.tool_behavior)
        self.playback = PlaybackController(adapter, self.metrics, lv.playback_queue_chunks)
        self.engine = LiveVoiceEngine(
            session_id=session_id, scope=scope, cfg=cfg, roles=roles, names=names, provider=self.provider,
            session_config=session_cfg, adapter=adapter,
            participation=LiveParticipation(cfg, roles, classifier or RuleIntentClassifier(), thresholds),
            registry=registry or SessionRegistry(lv.max_concurrent_sessions), playback=self.playback, tools=tools,
            working=WorkingMemory(), meeting_memory=self.memory, control=self.control, bus=bus,
            metrics=self.metrics, behaviour=self.behaviour, expressions=self.expressions,
            memory_gate=self.memory_gate,
            params=LiveParams(floor_wait_ms=pr.floor_wait_ms, guidance_injection=pr.guidance_injection, hold_ms=lv.hold_ms, uplink_queue=lv.uplink_queue_chunks,
                              reconnect_attempts=lv.reconnect_attempts, reconnect_backoff_s=lv.reconnect_backoff_s,
                              local_barge_in=lv.local_barge_in, hybrid_end_of_speech=lv.hybrid_end_of_speech))

    async def start(self) -> None:
        context = ""
        if (self.memory_service is not None and self.memory_scope is not None
                and self.settings.memory.startup_context):
            context = await self.memory_service.startup_context(
                self.memory_scope, limit=self.settings.memory.search_limit,
                max_chars=self.settings.memory.context_max_chars)
            if context:
                self.metrics.inc("memory_startup_context_injected")
        await self.engine.start(startup_context=context)

    async def close(self) -> None:
        await self.engine.close()
        if self._owns_memory and self.memory_service is not None:
            try:
                await asyncio.wait_for(self.memory_service.close(), self.settings.memory.write_timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - shutdown must finish; the failure is counted, never hidden
                self.metrics.inc("memory_close_failures")

    def health(self) -> dict[str, Any]:
        e = self.engine
        return {"status": e.status, "epoch": e.epoch, "takeover": e.takeover_active, "muted": e.muted,
                "playing": self.playback.is_playing, "model": self.settings.live.model, "profile": self.profile.value,
                "key": "set" if self.settings.gemini.api_key else "missing",
                "memory": "enabled" if self.memory_service is not None else "disabled"}
