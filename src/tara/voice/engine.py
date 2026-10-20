"""LiveVoiceEngine: one voice session between a meeting transport and a VoiceProvider.

Responsibilities (and only these):
  meeting audio -> bounded uplink -> provider;  provider events -> gate -> playback -> meeting.
  Interruption, stale-audio rejection, participation gating, tool calls, reconnect,
  metrics. It never imports Google types; the provider is injected.

Concurrency: one event loop. Child tasks (uplink, downlink, meeting events, tools, timers) are
tracked and cancelled on close. Tool execution never runs on the audio paths.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .. import events as bus_events
from ..audio import EnergyVad, Segmenter, SegmentReady, SpeechStarted, resample_pcm16
from ..behaviour.controller import BehaviourController
from ..behaviour.expressions import Expression, ExpressionEmitter, emotion_for
from ..behaviour.signals import analyze as analyze_signals
from ..config import MeetingConfig, Role
from ..contracts import ActionOutcome, MeetingAdapter, MeetingEvent, MeetingEventKind, MeetingTarget, PCMChunk, ToolCall
from ..control.commands import CommandKind, ControlCommand
from ..control.state import ControlState
from ..errors import FatalProviderError, ProviderError, SessionLimitExceeded  # noqa: F401
from ..identity import ParticipantRegistry
from ..memory.intent import MemoryWriteGate
from ..metrics import Metrics
from ..participation import Attribution
from ..state import (
    ControlChanged,
    ConversationState,
    Interrupted,
    ResponseEnded,
    ResponseStarted,
    SpeakingChanged,
    StateEvent,
    ToolFinished,
    ToolQueued,
    UtteranceAdded,
    UtteranceRecord,
)
from ..tools import ToolExecutor
from .events import (
    Disconnected,
    GenerationComplete,
    GoAway,
    InputTranscript,
    OutputTranscript,
    ResponseAudio,
    ResumptionHandle,
    SessionReady,
    ToolCallRequested,
    ToolCallsCancelled,
    ToolRequest,
    TurnComplete,
    VoiceEvent,
    VoiceProvider,
    VoiceSession,
    VoiceSessionConfig,
)
from .events import Interrupted as ProviderInterrupted
from .memory import PROMOTABLE_KINDS, MeetingMemory, Scope, WorkingMemory
from .participation import GateDecision, LiveParticipation, Verdict
from .playback import PlaybackController
from .registry import SessionRegistry

SOFT_DENY = frozenset({"not_addressed", "no_transcript", "low_intent_confidence"})   # may flip as more transcript arrives
TOOL_RESULT_MAX_CHARS = 12_000


@dataclass
class LiveParams:
    hold_ms: int = 1500                # max time response audio waits for a participation verdict (placeholder, tune)
    hold_max_chunks: int = 200         # bound on held audio
    uplink_queue: int = 100            # bounded; oldest mic audio is dropped (and counted) if the network lags
    reconnect_attempts: int = 3
    reconnect_backoff_s: float = 0.5
    local_barge_in: bool = True        # stop playback on local speech BEFORE the provider reports it
    hybrid_end_of_speech: bool = False  # send audio_stream_end when local VAD sees speech end (benefit unverified)
    max_tool_tasks: int = 4
    max_tool_calls_per_turn: int = 8
    self_stream_id: str = "tara"
    attribution_window_s: float = 6.0
    min_local_speech_ms: int = 150     # blips shorter than this never cancel playback
    floor_wait_ms: int = 2500          # allowed speech waits this long for a participant who is mid-sentence
    guidance_injection: bool = True    # send style notes mid-session (supported per docs; effect unverified)
    leave_grace_s: float = 6.0         # how long a spoken goodbye may take before leaving anyway
    action_timeout_s: float = 5.0      # platform actions (mute, camera, picture) must answer within this


class _Gate:
    def __init__(self, rid: int, origin: str, now: float):
        self.rid, self.origin, self.created = rid, origin, now
        self.state = "pending"             # pending | allowed | denied
        self.reason = ""
        self.held: deque[ResponseAudio] = deque()
        self.first_audio_at = now
        self.timer: asyncio.Task | None = None
        self.evaluating = False
        self.playing = False
        self.waiting_floor = False
        self.turn_done = False
        self.text = ""                      # what Tara said (output transcript), for memory


class LiveVoiceEngine:
    def __init__(self, *, session_id: str, scope: Scope, cfg: MeetingConfig, roles: dict[str, Role],
                 names: dict[str, str], provider: VoiceProvider, session_config: VoiceSessionConfig,
                 adapter: MeetingAdapter, participation: LiveParticipation, registry: SessionRegistry,
                 playback: PlaybackController | None = None, tools: ToolExecutor | None = None,
                 working: WorkingMemory | None = None, meeting_memory: MeetingMemory | None = None,
                 bus: bus_events.EventBus | None = None, metrics: Metrics | None = None,
                 params: LiveParams | None = None, clock: Callable[[], float] = time.monotonic,
                 participants: ParticipantRegistry | None = None, behaviour: BehaviourController | None = None,
                 expressions: ExpressionEmitter | None = None, identity_image: str = '',
                 control: ControlState | None = None, memory_gate: MemoryWriteGate | None = None):
        self.session_id, self.scope, self.cfg, self.roles, self.names = session_id, scope, cfg, roles, names
        self.provider, self.session_config, self.adapter = provider, session_config, adapter
        self.participation, self.sessions, self.tools = participation, registry, tools
        self.metrics = metrics or Metrics()
        self.clock, self.p = clock, params or LiveParams()
        self.playback = playback or PlaybackController(adapter, self.metrics, clock=clock)
        self.behaviour, self.expressions = behaviour, expressions
        self.playback.on_finished = self._on_playback_finished
        self.working = working or WorkingMemory()
        self.meeting_memory = meeting_memory
        self.bus = bus or bus_events.EventBus()
        self.participants = participants or ParticipantRegistry()
        self.state = ConversationState(meeting_id=scope.meeting_id, mode=cfg.mode.value)
        self.status = "idle"            # idle | connecting | active | reconnecting | closed
        self.epoch = 0
        self.takeover_active = False
        self.control = control or ControlState()
        self.memory_gate = memory_gate
        self.identity_image = identity_image
        self._ctrl_done = False
        self._ctrl_text = ""            # turn text already checked for a command (duplicate finals must not re-fire)
        self._deny_turn_audio = False   # a command turn is never answered by the model; clear at turn end
        self._local_interrupt_rid: int | None = None   # a local barge-in already split the turn for this response
        self._platform_muted = False
        self.approvals_needed: list[str] = []
        self._session: VoiceSession | None = None
        self._session_gen = 0
        self._resume: tuple[str, bool] | None = None
        self._closing = False
        self._closed = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._tool_tasks: dict[str, asyncio.Task] = {}
        self._tool_sem = asyncio.Semaphore(self.p.max_tool_tasks)
        self._tool_calls_turn = 0
        self._uplink: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=self.p.uplink_queue)
        self._segmenters: dict[str, Segmenter] = {}
        self._speaker_seen: dict[str, float] = {}
        self._active_streams: set[str] = set()
        self._overlap = False
        self._gates: dict[int, _Gate] = {}
        self._suppress_through = 0
        self._last_guidance = ""
        self._rid_base = 0              # provider response ids restart per session; ours never do
        self._max_rid = 0
        self._turn_text = ""
        self._turn_end_at: float | None = None
        self._origin_next = "participant"
        self._recover_lock = asyncio.Lock()
        self._seen_events: deque[str] = deque(maxlen=256)

    # ---- helpers -----------------------------------------------------------------------
    def _emit(self, name: str, **data: Any) -> None:
        self.bus.publish(bus_events.Event(name, int(self.clock() * 1000), self.epoch,
                                          {"session": self.session_id, **data}))

    def _st(self, e: StateEvent) -> None:
        from ..errors import InvalidStateChange
        try:
            self.state.apply(e)
        except InvalidStateChange:
            self.metrics.inc("state_invalid")

    def _spawn(self, coro, name: str) -> asyncio.Task:
        t = asyncio.create_task(coro, name=f"{self.session_id}:{name}")
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    # ---- lifecycle ----------------------------------------------------------------------
    async def start(self, target: MeetingTarget | None = None, startup_context: str = "") -> None:
        self.sessions.acquire(self.session_id)       # raises SessionLimitExceeded / DuplicateSession
        self.status = "connecting"
        if startup_context:
            # Retrieved memory is sent once when the session opens; it is not re-sent per turn.
            self.session_config = dataclasses.replace(
                self.session_config,
                system_instruction=self.session_config.system_instruction + "\n\n" + startup_context)
        try:
            t0 = self.clock()
            await self.adapter.join(target or MeetingTarget())
            self._session = await self.provider.open(self.session_config, None)
            self.metrics.observe("live_connect_ms", (self.clock() - t0) * 1000)
        except BaseException:
            try:
                await self.adapter.leave()
            except Exception:  # noqa: BLE001, S110 - best effort while already failing
                pass
            self.sessions.release(self.session_id)
            self.status = "closed"
            self._closed.set()
            raise
        self.status = "active"
        self._session_gen += 1
        self._spawn(self._downlink(self._session, self._session_gen), "downlink")
        self._spawn(self._uplink_loop(), "uplink")
        self._spawn(self._meeting_loop(), "meeting")
        self._emit("live.started")

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def close(self) -> None:
        if self._closing:
            await self._closed.wait()
            return
        self._closing = True
        self.status = "closed"
        for t in list(self._tool_tasks.values()):
            t.cancel()
        for g in self._gates.values():
            if g.timer:
                g.timer.cancel()
        me = asyncio.current_task()
        tasks = [t for t in self._tasks if t is not me]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.playback.close()
        sess, self._session = self._session, None
        if sess is not None:
            await sess.close()
        try:
            await self.adapter.leave()
        finally:
            self.sessions.release(self.session_id)
            self._closed.set()
            self._emit("live.closed")

    # ---- meeting -> provider (uplink) -----------------------------------------------------
    async def _meeting_loop(self) -> None:
        try:
            async for me in self.adapter.events():
                await self._on_meeting_event(me)
        finally:
            if not self._closing:
                self._spawn(self.close(), "close_on_meeting_end")

    async def _on_meeting_event(self, me: MeetingEvent) -> None:
        if me.event_id in self._seen_events:
            return
        self._seen_events.append(me.event_id)
        k = me.kind
        try:
            if k is MeetingEventKind.AUDIO:
                await self.on_meeting_audio(me.payload["chunk"])
            elif k is MeetingEventKind.PARTICIPANT_JOINED:
                self.participants.join(me.payload["id"], me.payload["name"])
            elif k is MeetingEventKind.PARTICIPANT_LEFT:
                self.participants.leave(me.payload["id"])
            elif k is MeetingEventKind.PARTICIPANT_RENAMED:
                self.participants.rename(me.payload["id"], me.payload["name"])
            elif k is MeetingEventKind.RECONNECTED:
                await self._cancel_output("meeting_reconnected")   # speech in flight is never assumed delivered
            elif k is MeetingEventKind.ENDED:
                self._spawn(self.close(), "close_on_ended")
        except (KeyError, TypeError, ValueError):
            self.metrics.inc("meeting_event_invalid")

    async def on_meeting_audio(self, chunk: PCMChunk) -> None:
        if self._closing or chunk.stream_id == self.p.self_stream_id:
            return                                       # never feed Tara's own track back to the model
        if chunk.sample_rate <= 0 or len(chunk.data) % 2:
            self.metrics.inc("audio_invalid")
            return
        data = resample_pcm16(chunk.data, chunk.sample_rate, 16000)
        if self._uplink.full():
            try:
                self._uplink.get_nowait()                # mic audio older than the lag is worthless: drop oldest
                self.metrics.inc("live_uplink_dropped")
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
        self._uplink.put_nowait(data)
        self.metrics.gauge("live_uplink_depth", self._uplink.qsize())
        seg = self._segmenters.setdefault(chunk.stream_id, Segmenter(
            EnergyVad(), chunk.stream_id, min_speech_ms=self.p.min_local_speech_ms))
        for out in seg.feed(PCMChunk(data, 16000, chunk.ts_ms, chunk.stream_id)):
            if isinstance(out, SpeechStarted):
                await self._on_local_speech_started(out.stream_id)
            elif isinstance(out, SegmentReady):
                self._active_streams.discard(out.segment.stream_id)
                self._turn_end_at = self.clock()
                if not self._active_streams:
                    self._overlap = False
                self._on_local_speech_ended()
                if self.p.hybrid_end_of_speech and self._session is not None:
                    try:
                        await self._session.end_audio_stream()
                    except Exception:  # noqa: BLE001 - a failed hint must not kill the audio path
                        self.metrics.inc("live_send_errors")

    async def _uplink_loop(self) -> None:
        while True:
            data = await self._uplink.get()
            sess = self._session
            if data is None:
                continue
            if sess is None:
                self.metrics.inc("live_uplink_dropped_offline")     # reconnecting: mic audio has nowhere to go
                continue
            try:
                await sess.send_audio(data)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a send failure is a disconnect; recovery decides what happens next
                self.metrics.inc("live_send_errors")
                if not self._closing and self.status == "active":
                    self._spawn(self._recover("send_failed", True), "recover")

    # ---- local speech: barge-in + attribution ---------------------------------------------------
    async def _on_local_speech_started(self, stream_id: str) -> None:
        # Snapshot who was speaking BEFORE the new stream is recorded. If this is a barge-in, the
        # interrupted turn belongs to the previous speaker, not to the person interrupting now.
        att_before = self._attribution() if self.playback.is_playing and self._turn_text.strip() else None
        self._active_streams.add(stream_id)
        if len(self._active_streams) > 1:
            self._overlap = True
        self._speaker_seen[stream_id] = self.clock()
        if self.expressions is not None and not self.playback.is_playing and not self.takeover_active:
            self.expressions.emit(Expression.LISTENING)
        if self.p.local_barge_in and self.playback.is_playing:
            rid = self.playback.active_response
            g = self._gates.get(rid) if rid is not None else None
            if g is not None and g.state == "allowed":
                await self._barge_in("local", att_before=att_before)

    def _attribution(self) -> Attribution:
        now = self.clock()
        recent = [(s, t) for s, t in self._speaker_seen.items()
                  if now - t <= self.p.attribution_window_s and s in self.names and s != self.p.self_stream_id]
        if self._overlap or not recent:
            return Attribution(None, 0.0, self._overlap)    # simultaneous streams: unknown, never guessed
        # Sequential speakers are not ambiguous: the stream that most recently began speaking is the
        # speaker whose transcript we just received (per-participant audio is platform evidence).
        stream, _ = max(recent, key=lambda st: st[1])
        return Attribution(stream, 0.99, False)

    # ---- provider -> meeting (downlink) -----------------------------------------------------------
    async def _downlink(self, session: VoiceSession, gen: int) -> None:
        try:
            async for ev in session.events():
                if gen != self._session_gen:
                    return                               # a newer session replaced this one
                await self._on_event(ev)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let the consumer die silently
            self.metrics.inc("live_downlink_errors")
            if not self._closing and gen == self._session_gen:
                self._spawn(self._recover("downlink_error", True), "recover")
            return
        if not self._closing and gen == self._session_gen and self.status == "active":
            self._spawn(self._recover("stream_ended", True), "recover")

    async def _on_event(self, ev: VoiceEvent) -> None:
        if isinstance(ev, (ResponseAudio, OutputTranscript, GenerationComplete, TurnComplete, ProviderInterrupted)):
            ev = dataclasses.replace(ev, response_id=ev.response_id + self._rid_base)   # session-local id -> engine-global id
            self._max_rid = max(self._max_rid, ev.response_id)
        if isinstance(ev, SessionReady):
            self.metrics.inc("live_session_ready")
            self.metrics.observe("live_session_ready_ms", ev.connect_ms)
            self._emit("live.session_ready", connect_ms=round(ev.connect_ms, 1))
        elif isinstance(ev, ResumptionHandle):
            self._resume = (ev.handle, ev.resumable)
        elif isinstance(ev, InputTranscript):
            self.metrics.inc("live_input_transcripts")
            if ev.finished:
                self.metrics.inc("live_input_transcript_finals")
            self._turn_text = (self._turn_text + " " + ev.text).strip()
            if ev.finished and self.memory_gate is not None:
                self.memory_gate.observe(self._turn_text)
            self._behaviour_observe(self._turn_text, final=False)
            await self._handle_control(ev.finished)
            await self._reevaluate_pending()
        elif isinstance(ev, ResponseAudio):
            self.metrics.inc("live_response_audio_chunks")
            await self._on_audio(ev)
        elif isinstance(ev, OutputTranscript):
            self.metrics.inc("live_output_transcripts")
            g = self._gates.get(ev.response_id)
            if g is not None:
                g.text = (g.text + ev.text)[:2000]
        elif isinstance(ev, GenerationComplete):
            self.playback.finish(ev.response_id)
        elif isinstance(ev, TurnComplete):
            self.metrics.inc("live_turn_complete")
            await self._on_turn_complete(ev)
        elif isinstance(ev, ProviderInterrupted):
            await self._barge_in("provider", ev.response_id)
        elif isinstance(ev, ToolCallRequested):
            for call in ev.calls:
                self._tool_tasks[call.call_id] = self._spawn(self._run_tool(call, self._session_gen), f"tool:{call.name}")
        elif isinstance(ev, ToolCallsCancelled):
            for cid in ev.call_ids:
                t = self._tool_tasks.pop(cid, None)
                if t:
                    t.cancel()
                    self.metrics.inc("live_tool_cancelled")
        elif isinstance(ev, GoAway):
            self.metrics.inc("live_goaway")
            self._emit("live.goaway", time_left_s=ev.time_left_s)
            if not self._closing and self.status == "active":
                self._spawn(self._recover("goaway", True), "recover")
        elif isinstance(ev, Disconnected):
            self.metrics.inc("live_disconnects")
            self._emit("live.disconnected", reason=ev.reason, recoverable=ev.recoverable)
            if not self._closing and self.status == "active":
                self._spawn(self._recover(ev.reason, ev.recoverable), "recover")

    # ---- response audio, gate, playback ------------------------------------------------------------------
    async def _on_audio(self, ev: ResponseAudio) -> None:
        rid = ev.response_id
        if rid <= self._suppress_through or self.playback.is_stale(rid):
            self.metrics.inc("stale_audio_dropped")      # audio from a response we already cancelled
            return
        if self._deny_turn_audio:
            self.metrics.inc("command_audio_dropped")    # a recognized command is never answered as a question
            return
        g = self._gates.get(rid)
        now = self.clock()
        if g is None:
            origin, self._origin_next = self._origin_next, "participant"
            g = self._gates[rid] = _Gate(rid, origin, now)
            if self._turn_end_at is not None and now >= self._turn_end_at:
                self.metrics.observe("live_first_audio_ms", (now - self._turn_end_at) * 1000)
                self._turn_end_at = None
            self._st(ResponseStarted(f"r{rid}", self.epoch))
            self._emit("live.response_started", response=rid, origin=origin)
            g.timer = self._spawn(self._hold_timer(g), "hold")
            await self._evaluate(g)
        if g.state == "allowed" and not g.waiting_floor:
            self._deliver(g, ev)
        elif g.state == "pending" or (g.state == "allowed" and g.waiting_floor):
            if len(g.held) >= self.p.hold_max_chunks:
                g.held.popleft()
                self.metrics.inc("held_chunks_dropped")
            g.held.append(ev)
        else:
            self.metrics.inc("denied_audio_dropped")

    def _deliver(self, g: _Gate, ev: ResponseAudio) -> None:
        ok = self.playback.enqueue(g.rid, PCMChunk(ev.pcm, ev.sample_rate, int(self.clock() * 1000), "tara"))
        if ok and not g.playing:
            g.playing = True
            self._st(SpeakingChanged(True))
            self._on_speaking_started(g)

    async def _reevaluate_pending(self) -> None:
        for g in list(self._gates.values()):
            if g.state == "pending":
                await self._evaluate(g)

    async def _evaluate(self, g: _Gate, final: bool = False) -> None:
        if g.state != "pending" or g.evaluating:
            return
        g.evaluating = True
        try:
            text = self._turn_text
            if g.origin == "participant" and not text.strip() and not final:
                return                                   # nothing known about this turn yet: keep holding
            ctx = [f"{t[0]}: {t[1]}" for t in list(self.working.turns)[-6:]]
            d = await self.participation.evaluate(text, self._attribution(), origin=g.origin,
                                                  takeover_active=self.takeover_active, muted=self.muted, context=ctx,
                                                  control_state=self.control, tara_speaking=self.playback.is_playing)
            if g.state != "pending" or self._gates.get(g.rid) is not g:
                return                                   # cancelled / replaced while we awaited
            await self._apply_decision(g, d, final)
        finally:
            g.evaluating = False

    async def _apply_decision(self, g: _Gate, d: GateDecision, final: bool) -> None:
        if d.verdict is Verdict.ALLOW:
            self._finish_gate(g, "allowed", d.reason)
            if self._active_streams:                      # a participant is mid-sentence: do not talk over them
                g.waiting_floor = True
                self.metrics.inc("floor_waits")
                g.timer = self._spawn(self._floor_timer(g), "floor")
                return
            self._flush(g)
            return
        if d.verdict in (Verdict.TAKEOVER, Verdict.MUTE, Verdict.COMMAND) or d.command is not None:
            if not self._ctrl_done:                      # normally already enacted at transcript level
                await self._enact(d, self._attribution())
            self._finish_gate(g, "denied", d.reason)
            return
        if d.reason in SOFT_DENY and not final:
            return                                       # more transcript may still make this turn "addressed"
        self._finish_gate(g, "denied", d.reason)

    def _finish_gate(self, g: _Gate, state: str, reason: str) -> None:
        g.state, g.reason = state, reason
        if state == "denied":
            g.held.clear()
        if g.timer and g.timer is not asyncio.current_task():
            g.timer.cancel()
        self.metrics.inc(f"gate_{state}")
        self._emit("live.gate", response=g.rid, state=state, reason=reason)
        if state == "denied":
            self.playback.finish(g.rid)

    async def _hold_timer(self, g: _Gate) -> None:
        try:
            await asyncio.sleep(self.p.hold_ms / 1000)
        except asyncio.CancelledError:
            return
        if g.state == "pending":
            await self._evaluate(g, final=True)
            if g.state == "pending":
                self._finish_gate(g, "denied", "hold_timeout")   # fail silent

    async def _on_turn_complete(self, ev: TurnComplete) -> None:
        await self._handle_control(True)
        g = self._gates.get(ev.response_id)
        if g is not None:
            if g.state == "pending":
                await self._evaluate(g, final=True)
                if g.state == "pending":
                    self._finish_gate(g, "denied", "turn_ended_undecided")
            g.turn_done = True
            if not (g.waiting_floor and g.state == "allowed"):
                self._finalize_gate(g)          # a gate still waiting for the floor is finalized when it flushes or times out
        self._commit_participant_turn()
        self._prune_gates()
        self._deny_turn_audio = False       # the command's own turn is over; later replies may play
        self._tool_calls_turn = 0           # a new model turn gets a fresh bounded tool budget

    def _finalize_gate(self, g: _Gate) -> None:
        self.playback.finish(g.rid)
        if g.first_audio_at:
            self.metrics.observe("live_response_ms", (self.clock() - g.first_audio_at) * 1000)
        if g.state == "allowed" and g.text:
            self.working.add_turn("Tara", g.text)
            self._behaviour_note_response(g.text)
        self._st(ResponseEnded(f"r{g.rid}", "completed" if g.state == "allowed" else "failed"))
        if g.timer and g.timer is not asyncio.current_task():
            g.timer.cancel()
        self._gates.pop(g.rid, None)

    def _commit_participant_turn(self, att: Attribution | None = None) -> None:
        text, self._turn_text = self._turn_text.strip(), ""
        self._ctrl_done = False
        if not text:
            return
        self._behaviour_observe(text, final=True)
        att = att or self._attribution()
        who = self.names.get(att.participant_id or "", None)
        self.working.add_turn(who or "participant", text)
        self._st(UtteranceAdded(UtteranceRecord(f"t{self.state.version}", att.participant_id, who, att.confidence, text,
                                                int(self.clock() * 1000), att.overlap)))

    def _prune_gates(self) -> None:
        for rid in sorted(self._gates)[:-4]:             # bounded bookkeeping
            self._gates.pop(rid, None)

    # ---- interruption ---------------------------------------------------------------------------------------
    async def _barge_in(self, source: str, rid: int | None = None, att_before: Attribution | None = None) -> None:
        """Stop audible playback for the active response and kill its remaining audio.
        Never touches the provider session or other responses."""
        t0 = self.clock()
        target = rid if rid is not None else (self.playback.active_response or max(self._gates, default=0))
        if source == "local":
            # The interrupted participant turn is complete and must keep its own speaker; otherwise
            # the interrupter's transcript is appended to it and the whole turn is misattributed.
            self._commit_participant_turn(att_before)
            self._ctrl_text = ""
            self._local_interrupt_rid = target
        self._suppress_through = max(self._suppress_through, target)
        delivered = await self.playback.cancel(f"{source}_interrupt", up_to=target)
        g = self._gates.pop(target, None)
        if g is not None:
            if g.timer:
                g.timer.cancel()
            if g.text and delivered is not None:
                heard = g.text[: max(0, int(delivered / 1000 * 15))].rstrip()    # approx: ~15 chars/s spoken
                if heard:
                    self.working.add_turn("Tara", heard + " [interrupted]")
            self._st(ResponseEnded(f"r{g.rid}", "cancelled"))
        if self.state.tara_speaking or delivered is not None:
            self._st(Interrupted())
        self.epoch += 1
        if self.behaviour is not None:
            self.behaviour.note_interrupted()
        if self.expressions is not None:
            self.expressions.emit(Expression.INTERRUPTED, response_id=target)
        self.metrics.inc(f"live_interrupts.{source}")
        self.metrics.observe("live_interrupt_total_ms", (self.clock() - t0) * 1000)
        self._emit("live.interrupted", source=source, response=target, delivered_ms=delivered)
        if source == "provider":
            if self._local_interrupt_rid == target:
                self._local_interrupt_rid = None   # the local path already split the turn; keep the new transcript
            else:
                self._commit_participant_turn_reset()
        self._tool_calls_turn = 0

    def _commit_participant_turn_reset(self) -> None:
        self._turn_text = ""      # the interrupter's speech is a NEW turn; its transcript arrives after this event
        self._ctrl_done = False

    async def _cancel_output(self, reason: str) -> None:
        """Drop everything audible or pending (reconnect, takeover, meeting reconnect). Session stays up."""
        target = max(self._gates, default=self.playback.active_response or 0)
        self._suppress_through = max(self._suppress_through, target)
        await self.playback.cancel(reason, up_to=target)
        for g in self._gates.values():
            if g.timer:
                g.timer.cancel()
        self._gates.clear()
        if self.state.response_task is not None:
            self._st(ResponseEnded(self.state.response_task.trigger_event_id, "cancelled"))
        self.epoch += 1

    # ---- conversational behaviour (style only; never decides who may speak) --------------------------------
    def _behaviour_observe(self, text: str, final: bool) -> None:
        b = self.behaviour
        if b is None or not text.strip():
            return
        with self.metrics.time("behaviour_ms"):
            b.observe(text, final=final)
            note = b.guidance() if self.p.guidance_injection else None
        if note:
            self._spawn(self._send_guidance(note, self._session_gen), "guidance")

    def _behaviour_note_response(self, text: str) -> None:
        b = self.behaviour
        if b is None:
            return
        for f in b.note_response(text):
            self.metrics.inc(f"behaviour_flag.{f.name}")       # names and counts only, never the text
        b.refresh()
        note = b.guidance() if self.p.guidance_injection else None
        if note:
            self._spawn(self._send_guidance(note, self._session_gen), "guidance")

    async def _send_guidance(self, note: str, gen: int) -> None:
        sess = self._session
        if sess is None or gen != self._session_gen or self._closing:
            return
        try:
            await sess.send_context(note)       # turn_complete=False in the adapter: no reply is requested
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - style notes are best effort
            self.metrics.inc("behaviour_guidance_errors")
            return
        self._last_guidance = note
        self.metrics.inc("behaviour_guidance_sent")
        self._emit("live.guidance", length=len(note))

    def _on_speaking_started(self, g: _Gate) -> None:
        b = self.behaviour
        if b is not None:
            b.note_response_started()
        em = self.expressions
        if em is None:
            return
        em.emit(Expression.SPEAKING_STARTED, response_id=g.rid)
        if b is not None and b.last_plan is not None:
            plan, sig = b.last_plan, b.last_signals
            emo = emotion_for(plan.regime, plan.humour, plan.laughter, plan.expressiveness, sig.unexpected, sig.heavy)
            if emo is not None:
                em.emit(emo, response_id=g.rid, intensity=plan.expressiveness if plan.expressiveness != "none" else "subtle")

    def _on_playback_finished(self, result: Any) -> None:
        em = self.expressions
        if em is None:
            return
        em.emit(Expression.SPEAKING_STOPPED, response_id=result.response_id)
        em.emit(Expression.YIELDING_CONTROL if self.takeover_active else Expression.LISTENING)

    # ---- floor: never talk over a participant ----------------------------------------------------------------
    def _flush(self, g: _Gate) -> None:
        g.waiting_floor = False
        while g.held:
            self._deliver(g, g.held.popleft())
        if g.turn_done:
            self._finalize_gate(g)

    def _on_local_speech_ended(self) -> None:
        if self.expressions is not None and not self.playback.is_playing and not self.takeover_active \
                and analyze_signals(self._turn_text).addressed_by_name:
            self.expressions.emit(Expression.THINKING)
        if self._active_streams:
            return
        for g in list(self._gates.values()):
            if g.waiting_floor and g.state == "allowed":
                if g.timer and g.timer is not asyncio.current_task():
                    g.timer.cancel()
                self._flush(g)

    async def _floor_timer(self, g: _Gate) -> None:
        try:
            await asyncio.sleep(self.p.floor_wait_ms / 1000)
        except asyncio.CancelledError:
            return
        if g.waiting_floor and g.state == "allowed":
            g.waiting_floor = False
            self.metrics.inc("floor_wait_timeouts")
            self._finish_gate(g, "denied", "floor_timeout")        # the moment passed: stay quiet
            if g.turn_done:
                self._finalize_gate(g)

    # ---- participation controls ----------------------------------------------------------------------------------
    def release_takeover(self) -> None:
        self.takeover_active = False
        self._st(ControlChanged(False))
        if self.behaviour is not None:
            self.behaviour.note_yield(False)
        if self.expressions is not None:
            self.expressions.emit(Expression.LISTENING)

    @property
    def muted(self) -> bool:
        return self.control.silenced

    @muted.setter
    def muted(self, value: bool) -> None:
        self.control.speech_mode = "listen_only" if value else "active"

    def resume(self) -> None:
        """Leave listen-only / muted mode (programmatic equivalent of an authorized spoken 'you can speak again')."""
        self.control.speech_mode, self.control.set_by = "active", None
        if self.behaviour is not None:
            self.behaviour.note_muted(False)

    unmute = resume

    # ---- spoken control: recognized by code, authorized by policy, executed here ------------------------------------
    async def _handle_control(self, final: bool) -> None:
        if not final or self.status != "active":
            return
        text = self._turn_text.strip()
        if not text or text == self._ctrl_text:
            return                                   # nothing new: a duplicate final must not re-fire
        if not self._ctrl_text:
            self._deny_turn_audio = False            # a new utterance ends the previous command's suppression
        self._ctrl_text, self._ctrl_done = text, False
        att = self._attribution()
        ctx = [f"{t[0]}: {t[1]}" for t in list(self.working.turns)[-4:]]
        d = await self.participation.control(text, att, state=self.control,
                                             tara_speaking=self.playback.is_playing, context=ctx)
        if d is not None:
            await self._enact(d, att)

    async def _enact(self, d: GateDecision, att: Attribution) -> None:
        self._ctrl_done = True
        if d.verdict is Verdict.TAKEOVER:
            await self._enter_takeover()
        elif d.reason == "barge_in":
            # Anyone may stop her talking over them; this is not persistent control.
            self.metrics.inc("control.barge_in")
            self._deny_turn_audio = True
            await self._cancel_output("barge_in")
        elif d.verdict in (Verdict.MUTE, Verdict.COMMAND):
            await self._execute_command(d.command or ControlCommand(CommandKind.LISTEN_ONLY), att)
        elif d.command is not None:
            self._deny_turn_audio = True             # denied command: no model answer to it
            await self._refuse(d)
        self._finish_control_turn()

    def _finish_control_turn(self) -> None:
        """Record the command as a participant turn, then clear it so later fragments cannot re-trigger it."""
        self._commit_participant_turn()
        self._ctrl_text = ""

    async def _enter_takeover(self) -> None:
        self.takeover_active = True
        self.epoch += 1
        self._st(ControlChanged(True))
        self.metrics.inc("live_takeovers")
        if self.behaviour is not None:
            self.behaviour.note_yield(True)
        if self.expressions is not None:
            self.expressions.emit(Expression.YIELDING_CONTROL)
        await self._cancel_output("takeover")

    async def _adapter_call(self, name: str, *args: Any) -> ActionOutcome:
        fn = getattr(self.adapter, name, None)
        if fn is None:
            return ActionOutcome(False, "unsupported")
        try:
            out = await asyncio.wait_for(fn(*args), self.p.action_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a platform failure is reported, never allowed to break the session
            return ActionOutcome(False, type(exc).__name__)
        return out if isinstance(out, ActionOutcome) else ActionOutcome(True)

    async def _confirm(self, fact: str) -> None:
        """Ask the model to say ONE short true sentence about what the application just did (or could not do)."""
        try:
            await self.prompt("[Application message, not from a participant] " + fact)
        except ProviderError:
            self.metrics.inc("control_confirm_failed")

    async def _execute_command(self, cmd: ControlCommand, att: Attribution) -> None:
        k, st, caps = cmd.kind, self.control, self.adapter.capabilities
        self.metrics.inc(f"control.{k.value}")
        self._emit("live.command", kind=k.value, by=att.participant_id)          # ids only, never text
        if k in (CommandKind.MIC_MUTE, CommandKind.LISTEN_ONLY):
            st.speech_mode = "muted" if k is CommandKind.MIC_MUTE else "listen_only"
            st.set_by = att.participant_id
            if self.behaviour is not None:
                self.behaviour.note_muted(True)
            if self.expressions is not None:
                self.expressions.emit(Expression.LISTENING)
            await self._cancel_output("silenced")                                 # stop talking now; keep listening
            if k is CommandKind.MIC_MUTE and caps.can_mute_self:
                out = await self._adapter_call("set_microphone", False)
                self._platform_muted = out.ok
                if not out.ok:
                    self.metrics.inc("control_platform_failed")                   # app-level silence still holds
            return                                                                # silence is the confirmation
        if k is CommandKind.RESUME:
            self.resume()
            if self._platform_muted:
                out = await self._adapter_call("set_microphone", True)
                self._platform_muted = not out.ok
            await self._confirm("You may speak again. Say so naturally in a few words.")
        elif k is CommandKind.LEAVE:
            self._spawn(self._leave(), "leave")
        elif k in (CommandKind.CAMERA_ON, CommandKind.CAMERA_OFF):
            on = k is CommandKind.CAMERA_ON
            out = await self._adapter_call("set_camera", on)
            if out.ok:
                st.camera_on = on
            await self._confirm(f"Your camera is now {'on' if on else 'off'}. Say so briefly." if out.ok else
                                "You could not change the camera from here. Say honestly that you could not, in a few words.")
        elif k is CommandKind.SHOW_IDENTITY:
            out = await self._adapter_call("show_image", self.identity_image)
            st.identity_shown = out.ok or st.identity_shown
            await self._confirm("You are now showing your picture. Say briefly that it is you." if out.ok else
                                "You could not show a picture here. Say honestly that you could not, in a few words.")
        elif k in (CommandKind.NOTES_ON, CommandKind.NOTES_OFF):
            await self._set_notes(k is CommandKind.NOTES_ON)
        elif k is CommandKind.CONFIRM_NOTE:
            await self._confirm_note(att)
        elif k is CommandKind.MUTE_OTHER:
            out = await self._adapter_call("mute_participant", cmd.target)
            await self._confirm("You muted that participant." if out.ok else
                                "You could not mute that participant. Say so honestly and briefly.")

    async def _refuse(self, d: GateDecision) -> None:
        """A recognized command that policy or the platform does not allow: say so honestly, never pretend."""
        assert d.command is not None
        parts = d.reason.split(":")
        kind = parts[1] if len(parts) > 1 else "unknown"
        reason = parts[2] if len(parts) > 2 else ""
        self.metrics.inc(f"control_denied.{kind}.{reason}")
        self._emit("live.command_denied", kind=kind, reason=reason)
        if reason == "identity_low":
            return                                                                # cannot tell who asked: stay quiet
        if kind == "resume":
            return                                                                # she is silenced; a refusal adds nothing
        if reason == "unsupported":
            await self._confirm("Someone asked for something this meeting setup cannot do. Say honestly that you can't do that here, briefly.")
        elif reason == "needs_control_role":
            await self._confirm("Someone without the needed role asked for an action. Say briefly that you will leave that to the organizer.")

    async def _leave(self) -> None:
        self.control.left = True
        if not self.control.silenced and not self.takeover_active:
            n = len(self.playback.results)
            await self._confirm("You are leaving the meeting now. Say a very short goodbye.")
            end = self.clock() + self.p.leave_grace_s
            while self.clock() < end and len(self.playback.results) == n:
                await asyncio.sleep(0.05)
        await self.close()

    async def _set_notes(self, on: bool) -> None:
        self.control.notes_active = on
        note = ("Notes mode is on. Without interrupting anyone, record decisions, action items with owners, deadlines and "
                "open questions with the record_note tool, only what was actually said, and mark suggestions as suggestions."
                if on else "Notes mode is off. Stop recording notes unless asked.")
        await self._send_note(note)

    async def _confirm_note(self, att: Attribution) -> None:
        """Promote the newest candidate decision/action item. Only authorized callers reach this."""
        rec = (self.meeting_memory.promote_latest(self.scope, PROMOTABLE_KINDS, att.participant_id or "")
               if self.meeting_memory is not None else None)
        if rec is None:
            self.metrics.inc("control_note_confirm_empty")
            await self._confirm("There is no candidate decision or action item to confirm. "
                                "Say briefly that there is nothing to confirm yet.")
            return
        self.metrics.inc("control_note_confirmed")
        self._emit("live.note_confirmed", kind=rec.kind)          # kind only: note bodies stay out of events
        await self._confirm(f"The latest candidate {rec.kind.replace('_', ' ')} is now confirmed. "
                            "Say briefly that it is confirmed.")

    async def _send_note(self, text: str) -> None:
        sess = self._session
        if sess is None or self._closing:
            return
        try:
            await sess.send_context("Internal note, do not speak it and do not answer it: " + text)
        except Exception:  # noqa: BLE001
            self.metrics.inc("control_note_failed")

    async def prompt(self, text: str) -> None:
        """Tara-initiated speech (e.g. the next interview question). Still subject to silent/takeover/muted."""
        if self._session is None:
            raise ProviderError("no active session")
        self._origin_next = "app"
        await self._session.send_text(text)

    # ---- tools -------------------------------------------------------------------------------------------------------
    async def _run_tool(self, req: ToolRequest, gen: int) -> None:
        sess = self._session
        queued_at = time.perf_counter()
        try:
            if self._tool_calls_turn >= self.p.max_tool_calls_per_turn:
                self.metrics.inc("live_tool_budget_denied")
                payload: dict[str, Any] = {"error": "tool_budget_exceeded",
                                           "details": {"limit": self.p.max_tool_calls_per_turn}}
            elif self.tools is None:
                payload = {"error": "tools_unavailable"}
            elif self._tool_sem.locked() and len(self._tool_tasks) > self.p.max_tool_tasks * 2:
                payload = {"error": "busy"}
            else:
                self._tool_calls_turn += 1
                async with self._tool_sem:
                    self.metrics.observe("live_tool_queue_ms", (time.perf_counter() - queued_at) * 1000)
                    cid = f"live:{self.session_id}:{req.call_id}"
                    self._st(ToolQueued(cid, ToolCall(req.name, {})))
                    try:
                        if self.memory_gate is not None and self._turn_text.strip():
                            # Input transcription can lag the tool call; use the turn text we have.
                            self.memory_gate.observe(self._turn_text)
                        if self.tools.allows_live_write(req.name):
                            # The handler re-checks the explicit authorization (notes mode or memory gate);
                            # it writes, so it cannot go through execute_read_only.
                            res = await self.tools.execute(ToolCall(req.name, req.args), cid)
                        else:
                            res = await self.tools.execute_read_only(ToolCall(req.name, req.args), cid)
                    finally:
                        self._st(ToolFinished(cid))
                if res.ok:
                    payload = {"result": _jsonable(res.output)}
                else:
                    payload = {"error": res.error or "failed"}
                    if res.retryable:
                        payload["retryable"] = True
                    if res.details:
                        payload["details"] = _jsonable(res.details)
                    if res.error == "side_effect_requires_approval" or res.pending_approval:
                        self.approvals_needed.append(req.name)
            if gen != self._session_gen or self._session is not sess or sess is None or self._closing:
                self.metrics.inc("live_tool_result_dropped")       # belongs to a dead session
                return
            await sess.send_tool_result(req.call_id, req.name, payload)
            self.metrics.inc("live_tool_calls")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a tool path failure must never reach the audio paths
            self.metrics.inc("live_tool_errors")
        finally:
            self._tool_tasks.pop(req.call_id, None)

    # ---- recovery -----------------------------------------------------------------------------------------------------
    async def _recover(self, reason: str, recoverable: bool) -> None:
        if self._recover_lock.locked() or self._closing:
            return
        async with self._recover_lock:
            if self._closing or self.status != "active":
                return
            self.status = "reconnecting"
            self._emit("live.reconnecting", reason=reason)
            await self._cancel_output("reconnect")
            for t in list(self._tool_tasks.values()):
                t.cancel()
            self._tool_tasks.clear()
            old, self._session = self._session, None
            self._session_gen += 1
            if old is not None:
                try:
                    await old.close()
                except Exception:  # noqa: BLE001, S110
                    pass
            if not recoverable:
                await self._fail("not_recoverable")
                return
            for attempt in range(self.p.reconnect_attempts):
                self.metrics.inc("live_reconnect_attempts")
                handle = self._resume[0] if self._resume and self._resume[1] else None
                try:
                    new = await self.provider.open(self.session_config, handle)
                except FatalProviderError:
                    await self._fail("fatal_provider_error")
                    return
                except asyncio.CancelledError:
                    raise
                except ProviderError:
                    await asyncio.sleep(self.p.reconnect_backoff_s * (2 ** attempt))
                    continue
                self._session = new
                self._session_gen += 1
                self._rid_base = max(self._max_rid, self._suppress_through)   # new session numbers from 1 again
                self.status = "active"
                self._spawn(self._downlink(new, self._session_gen), "downlink")
                if handle is None:
                    try:
                        await new.send_context(self.working.render())     # fresh session: re-seed from OUR memory
                        if self._last_guidance:
                            await new.send_context(self._last_guidance)
                    except Exception:  # noqa: BLE001
                        self.metrics.inc("live_send_errors")
                self.metrics.inc("live_reconnects")
                self._emit("live.reconnected", resumed=handle is not None, attempt=attempt + 1)
                return
            await self._fail("reconnect_exhausted")

    async def _fail(self, reason: str) -> None:
        self.metrics.inc("live_session_failures")
        self._emit("live.failed", reason=reason)
        self._spawn(self.close(), "close_after_failure")


def _jsonable(value: Any) -> Any:
    """Tool output goes to the model as data: make it JSON-safe and bounded."""
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = json.dumps(str(value))
    if len(text) > TOOL_RESULT_MAX_CHARS:
        return {"truncated": text[:TOOL_RESULT_MAX_CHARS]}
    return json.loads(text)
