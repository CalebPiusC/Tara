"""MeetingEngine: async orchestration of the whole pipeline on one event loop.

adapter events -> segmenter -> STT -> attribution -> intent -> participation gate
-> conversation (proposal) -> runtime approval -> TTS -> adapter playback.

Single-writer rule: only this class mutates MeetingRuntime. Generation/playback
run in one cancellable task tagged with the epoch it started in.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable

from . import events as ev
from .audio import Segmenter, SegmentReady, SpeechStarted, resample_pcm16
from .classify import IntentClassifier
from .config import MeetingConfig, Role
from .contracts import (AudioSegment, MeetingAdapter, MeetingEvent, MeetingEventKind, MeetingTarget,
                        PCMChunk, SpeakerHint, SpeechRecognizer, SpeechSynthesizer, HintKind)
from .conversation import CLARIFY_SPEAKER, ConversationEngine, RuntimeView, TranscriptLine
from .errors import ProviderError
from .identity import ParticipantRegistry, SpeakerAttributor
from .interview import InterviewEngine
from .participation import Action, Attribution, Decision, IntentKind, Thresholds, Utterance, decide
from .reports import MeetingLog
from .runtime import MeetingRuntime, Proposal, State
from .tools import ToolExecutor
from .contracts import NullAvatarProvider, AvatarProvider

REFUSE_LINE = "That's outside what I can help with in this meeting."


@dataclass
class EngineParams:
    gen_timeout_s: float = 20.0
    max_proposal_age_ms: int = 8000
    defer_max_age_ms: int = 4000
    echo_ratio: float = 0.85
    echo_window_ms: int = 15000
    hint_window_ms: int = 3000
    self_stream_id: str = "tara"
    history_limit: int = 200
    stt_timeout_s: float = 30.0
    max_speech_s: float = 120.0
    floor_wait_ms: int = 3000
    segment_queue: int = 16


class MeetingEngine:
    def __init__(self, *, cfg: MeetingConfig, roles: dict[str, Role], names: dict[str, str],
                 adapter: MeetingAdapter, stt: SpeechRecognizer, tts: SpeechSynthesizer,
                 classifier: IntentClassifier, conversation: ConversationEngine, thresholds: Thresholds,
                 registry: ParticipantRegistry, attributor: SpeakerAttributor,
                 segmenter_factory: Callable[[str], Segmenter],
                 bus: ev.EventBus | None = None, clock: Callable[[], int] | None = None,
                 interview: InterviewEngine | None = None, tools: ToolExecutor | None = None,
                 avatar: AvatarProvider | None = None, params: EngineParams | None = None):
        self.cfg, self.roles, self.names = cfg, roles, names
        self.adapter, self.stt, self.tts = adapter, stt, tts
        self.classifier, self.conversation, self.th = classifier, conversation, thresholds
        self.registry, self.attributor = registry, attributor
        self._seg_factory = segmenter_factory
        self.bus = bus or ev.EventBus()
        self.clock = clock or (lambda: int(time.monotonic() * 1000))
        self.interview, self.tools = interview, tools
        self.avatar = avatar or NullAvatarProvider()
        self.p = params or EngineParams()
        self.rt = MeetingRuntime(self.p.max_proposal_age_ms, policy_silent=cfg.policy.value == "silent")
        self.log = MeetingLog()
        self.lines: deque[TranscriptLine] = deque(maxlen=self.p.history_limit)
        self.spoken: list[dict] = []
        self._segmenters: dict[str, Segmenter] = {}
        self._hints: deque[SpeakerHint] = deque(maxlen=64)
        self._active_streams: set[str] = set()
        self._overlap = False
        self._task: asyncio.Task | None = None
        self._deferred: Utterance | None = None
        self._deferred_at = 0
        self._n = 0
        self._recent_tts: deque[tuple[str, int]] = deque(maxlen=8)
        self._segq: asyncio.Queue[tuple[AudioSegment, bool] | None] = asyncio.Queue(maxsize=self.p.segment_queue)
        self._worker: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._inflight = 0
        self.dropped_segments = 0

    # ---- lifecycle --------------------------------------------------------
    def _emit(self, name: str, **data) -> None:
        self.bus.publish(ev.Event(name, self.clock(), self.rt.epoch, data))

    def _go(self, s: State) -> None:
        self.rt.transition(s)
        self._emit(ev.STATE_CHANGED, state=s.value)

    async def start(self, target: MeetingTarget | None = None) -> None:
        self._go(State.CONNECTING)
        await self.adapter.join(target or MeetingTarget())
        self._go(State.LISTENING)
        self._worker = asyncio.create_task(self._segment_worker())

    async def run(self) -> None:
        async for e in self.adapter.events():
            await self.handle_event(e)
        await self.shutdown()

    async def drain(self) -> None:
        """Wait until all queued audio segments have been fully handled (tests, shutdown)."""
        await self._segq.join()

    async def shutdown(self) -> None:
        if self.rt.state is State.STOPPED:
            return
        await self._cancel_task()
        w, self._worker = self._worker, None
        if w and not w.done():
            w.cancel()
            try:
                await w
            except asyncio.CancelledError:
                pass
        self.rt.state = State.STOPPED
        self._emit(ev.STATE_CHANGED, state="STOPPED")
        await self.adapter.leave()

    # ---- adapter events ----------------------------------------------------
    async def handle_event(self, e: MeetingEvent) -> None:
        if self.rt.state is State.STOPPED:
            return
        if not self.rt.accept_event(f"m:{e.event_id}"):
            return
        try:
            await self._dispatch(e)
        except (KeyError, TypeError, ValueError) as exc:   # malformed adapter payload: drop the event, keep the meeting
            self._emit("event.invalid", kind=e.kind.value, error=type(exc).__name__)
        await self.tick()

    async def _dispatch(self, e: MeetingEvent) -> None:
        k = e.kind
        if k is MeetingEventKind.PARTICIPANT_JOINED:
            self.registry.join(e.payload["id"], e.payload["name"])
        elif k is MeetingEventKind.PARTICIPANT_LEFT:
            self.registry.leave(e.payload["id"])
        elif k is MeetingEventKind.PARTICIPANT_RENAMED:
            self.registry.rename(e.payload["id"], e.payload["name"])
        elif k is MeetingEventKind.SPEAKER_HINT:
            self._hints.append(e.payload["hint"])
        elif k is MeetingEventKind.AUDIO:
            await self.ingest_audio(e.payload["chunk"])
        elif k is MeetingEventKind.RECONNECTED:
            await self._on_reconnect()
        elif k is MeetingEventKind.ENDED:
            await self.shutdown()

    async def _on_reconnect(self) -> None:
        await self._cancel_task()          # speech in flight is NOT assumed delivered
        self.rt.on_reconnect()
        self._emit(ev.STATE_CHANGED, state="RECOVERING")
        self._deferred = None
        self._go(State.LISTENING)

    # ---- audio in ------------------------------------------------------------
    async def ingest_audio(self, chunk: PCMChunk) -> None:
        if self.rt.state is State.STOPPED:
            return
        if chunk.stream_id == self.p.self_stream_id:
            return  # never process Tara's own track
        if chunk.sample_rate <= 0 or len(chunk.data) % 2:
            self._emit("audio.invalid", stream=chunk.stream_id)
            return
        seg = self._segmenters.setdefault(chunk.stream_id, self._seg_factory(chunk.stream_id))
        if chunk.sample_rate != seg.vad.sample_rate:
            chunk = PCMChunk(resample_pcm16(chunk.data, chunk.sample_rate, seg.vad.sample_rate),
                             seg.vad.sample_rate, chunk.ts_ms, chunk.stream_id)
        for out in seg.feed(chunk):
            if isinstance(out, SpeechStarted):
                await self._on_speech_started(out)
            elif isinstance(out, SegmentReady):
                self._active_streams.discard(out.segment.stream_id)
                self.rt.others_speaking.discard(out.segment.stream_id)
                self._emit(ev.VAD_ENDED, stream=out.segment.stream_id)
                self._enqueue(out.segment, self._overlap)
                if not self._active_streams:
                    self._overlap = False

    def _enqueue(self, seg: AudioSegment, overlap: bool) -> None:
        """Audio ingestion never waits on STT/LLM: slow processing must not delay barge-in detection.
        Bounded queue; when full the OLDEST pending segment is dropped and counted."""
        if self._segq.full():
            try:
                self._segq.get_nowait()
                self._segq.task_done()
                self.dropped_segments += 1
                self._emit("segment.dropped", total=self.dropped_segments)
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
        self._segq.put_nowait((seg, overlap))

    async def _segment_worker(self) -> None:
        while True:
            item = await self._segq.get()
            try:
                if item is not None:
                    await self.process_segment(item[0], item[1])
            except asyncio.CancelledError:
                raise
            except Exception as exc:   # last-resort boundary: one bad segment must not kill the meeting
                self._emit("segment.failed", error=type(exc).__name__)
            finally:
                self._segq.task_done()

    async def _on_speech_started(self, s: SpeechStarted) -> None:
        self._active_streams.add(s.stream_id)
        self.rt.others_speaking.add(s.stream_id)
        if len(self._active_streams) > 1:
            self._overlap = True
        self._emit(ev.VAD_STARTED, stream=s.stream_id)
        if self.rt.state is State.SPEAKING:
            # immediate barge-in: cancel before any transcript exists (see LIVE_VOICE_REFERENCE)
            await self._interrupt()

    async def process_segment(self, seg: AudioSegment, overlap: bool = False) -> None:
        try:
            tr = await asyncio.wait_for(self.stt.transcribe(seg), self.p.stt_timeout_s)
        except (ProviderError, asyncio.TimeoutError):
            self._emit("transcript.failed", start_ms=seg.start_ms)
            self._resume_if_interrupted()
            return
        text = tr.text.strip()
        if not text or self._is_echo(text):
            self._resume_if_interrupted()
            return
        hints = [h for h in self._hints if abs(h.ts_ms - seg.start_ms) <= self.p.hint_window_ms]
        if seg.stream_id not in ("mix", self.p.self_stream_id) and self.registry.get(seg.stream_id):
            hints.append(SpeakerHint(HintKind.STREAM, seg.start_ms, participant_id=seg.stream_id))
        sa = self.attributor.attribute(seg.start_ms, seg.end_ms, hints, overlap=overlap)
        self._n += 1
        u = Utterance(f"u{self._n}", text, Attribution(sa.participant_id, sa.confidence, sa.overlap))
        self._emit(ev.ATTRIBUTION, event=u.event_id, participant=sa.participant_id, confidence=sa.confidence,
                   evidence=list(sa.evidence), overlap=sa.overlap)
        await self.handle_utterance(u, seg.end_ms)

    def _resume_if_interrupted(self) -> None:
        """Barge-in with no usable transcript must not leave the runtime stuck."""
        if self.rt.state is State.INTERRUPTED:
            self._go(State.EVALUATING_TURN)
            self._go(State.LISTENING)

    def _is_echo(self, text: str) -> bool:
        now = self.clock()
        t = text.lower()
        return any(now - at <= self.p.echo_window_ms and SequenceMatcher(None, t, s.lower()).ratio() >= self.p.echo_ratio
                   for s, at in self._recent_tts)

    # ---- the decision path ------------------------------------------------------
    async def handle_utterance(self, u: Utterance, end_ms: int = 0) -> None:
        if not self.rt.accept_event(f"u:{u.event_id}"):
            return
        who = self.names.get(u.attribution.participant_id or "", None)
        self.lines.append(TranscriptLine(who, u.attribution.confidence, u.text))
        self._emit(ev.TRANSCRIPT_FINAL, event=u.event_id, text_len=len(u.text))
        if self.interview is not None:
            self.interview.record_answer(u)
        ctx = [f"{ln.speaker or 'unknown'}: {ln.text}" for ln in list(self.lines)[-6:]]
        intent = await self.classifier.classify(u.text, ctx)   # slow, outside the lock; state is read AFTER it

        async with self._lock:
            if self.rt.state is State.STOPPED:
                return
            if self.rt.state is State.SPEAKING:
                if decide(u, intent, self.rt, self.cfg, self.roles, self.th).action is Action.TAKEOVER:
                    await self._takeover()
                    return
                await self._interrupt()
            elif self.rt.state in (State.GENERATING, State.VALIDATING, State.WAITING_TO_SPEAK) \
                    and intent.kind is IntentKind.HUMAN_TO_HUMAN:
                await self._make_stale()
                return

            if self.rt.state in (State.LISTENING, State.INTERRUPTED):
                self._go(State.EVALUATING_TURN)
            d = decide(u, intent, self.rt, self.cfg, self.roles, self.th)
            self._emit(ev.PARTICIPATION, event=u.event_id, action=d.action.value, reason=d.reason)
            await self._act(u, d)

    async def _act(self, u: Utterance, d: Decision) -> None:
        a = d.action
        if a is Action.TAKEOVER:
            await self._takeover()
        elif a is Action.MUTE:
            self.rt.policy_silent = True
            self._back_to_listening()
        elif a is Action.ALLOW:
            self._start(u, None)
        elif a is Action.ASK_CLARIFICATION:
            self._start(u, CLARIFY_SPEAKER)
        elif a is Action.REFUSE:
            self._start(u, REFUSE_LINE)
        elif a is Action.WAIT:
            self._deferred, self._deferred_at = u, self.clock()
            self._back_to_listening()
        else:
            self._back_to_listening()

    def _back_to_listening(self) -> None:
        if self.rt.state is State.EVALUATING_TURN:
            self._go(State.LISTENING)

    async def tick(self) -> None:
        """Retry a deferred (floor-busy) trigger once the floor is free; bounded by age."""
        u = self._deferred
        if u is None or self.rt.state is not State.LISTENING:
            return
        if self.clock() - self._deferred_at > self.p.defer_max_age_ms:
            self._deferred = None
        elif not self.rt.others_speaking and not self.rt.takeover_active:
            self._deferred = None
            self._go(State.EVALUATING_TURN)
            self._start(u, None)

    # ---- generation + playback ---------------------------------------------------
    def _start(self, u: Utterance, fixed: str | None) -> None:
        self._go(State.GENERATING)
        self._task = asyncio.create_task(self._respond(u, fixed, self.rt.epoch, self.clock()))

    async def _respond(self, u: Utterance, fixed: str | None, epoch: int, created: int) -> None:
        """Task boundary: any unexpected failure ends in silence + LISTENING, never a wedged state."""
        try:
            await self._respond_inner(u, fixed, epoch, created)
        except asyncio.CancelledError:
            raise
        except Exception as exc:   # noqa: BLE001 - deliberate last-resort boundary, reported not swallowed
            self._emit(ev.PROPOSAL_REJECTED, event=u.event_id, reason=f"internal:{type(exc).__name__}")
            if self.rt.epoch == epoch and self.rt.state in (State.GENERATING, State.VALIDATING, State.WAITING_TO_SPEAK, State.SPEAKING):
                self.rt.state = State.LISTENING   # bypass table on purpose: recovery from an internal error
                self._emit(ev.STATE_CHANGED, state="LISTENING")

    async def _respond_inner(self, u: Utterance, fixed: str | None, epoch: int, created: int) -> None:
        text: str | None = fixed
        tool = None
        try:
            if text is None:
                q = self.interview.current_question if self.interview else None
                view = RuntimeView(list(self.lines), q.text if q else None)
                v = await asyncio.wait_for(self.conversation.propose(u.text, view), self.p.gen_timeout_s)
                text, tool = v.text, v.tool
                if v.unanswered:
                    self.log.unanswered.append(u.text)
        except (ProviderError, asyncio.TimeoutError) as e:
            self._emit(ev.PROPOSAL_REJECTED, event=u.event_id, reason=type(e).__name__)
            self._abandon(epoch)
            return
        if not text:
            self._emit(ev.PROPOSAL_REJECTED, event=u.event_id, reason="empty")
            self._abandon(epoch)
            return
        if self.rt.state is not State.GENERATING or self.rt.epoch != epoch:
            self._emit(ev.PROPOSAL_REJECTED, event=u.event_id, reason="stale_epoch")
            return
        prop = Proposal(f"p_{u.event_id}", u.event_id, epoch, created, text)
        self._emit(ev.PROPOSAL_CREATED, event=u.event_id)
        self._go(State.VALIDATING)
        ok = self.rt.approve(prop, self.clock())
        if not ok.approved and ok.reason == "floor_busy":
            # someone is talking: wait (bounded) for the floor instead of dropping a direct answer
            self._go(State.WAITING_TO_SPEAK)
            freed = await self._wait_floor(epoch)
            if not freed or self.rt.state is not State.WAITING_TO_SPEAK or self.rt.epoch != epoch:
                if self.rt.state is State.WAITING_TO_SPEAK and self.rt.epoch == epoch:
                    self._emit(ev.PROPOSAL_REJECTED, event=u.event_id, reason="floor_timeout")
                    self._go(State.LISTENING)
                return
            ok = self.rt.approve(prop, self.clock())
            if not ok.approved:
                self._emit(ev.PROPOSAL_REJECTED, event=u.event_id, reason=ok.reason)
                self._go(State.LISTENING)
                return
        elif not ok.approved:
            self._emit(ev.PROPOSAL_REJECTED, event=u.event_id, reason=ok.reason)
            self._go(State.LISTENING)
            return
        else:
            self._go(State.WAITING_TO_SPEAK)
        self._emit(ev.PROPOSAL_APPROVED, event=u.event_id)
        self.rt.mark_answered(u.event_id)
        if tool is not None and self.tools is not None:      # side effects only AFTER runtime approval
            self._emit(ev.TOOL_REQUESTED, tool=tool.name)
            res = await self.tools.execute(tool, f"call:{u.event_id}")
            self._emit(ev.TOOL_EXECUTED if res.ok else ev.TOOL_DENIED, tool=tool.name, error=res.error)
            if res.pending_approval:
                self.log.approvals_needed.append(f"{tool.name}({u.event_id})")
            if self.rt.state is not State.WAITING_TO_SPEAK or self.rt.epoch != epoch:
                return   # the room changed while the tool ran
        await self._speak(text, epoch, u.event_id)

    async def _wait_floor(self, epoch: int) -> bool:
        deadline = self.clock() + self.p.floor_wait_ms
        while self.rt.others_speaking:
            if self.rt.epoch != epoch or self.clock() >= deadline:
                return False
            await asyncio.sleep(0.02)
        return True

    def _abandon(self, epoch: int) -> None:
        if self.rt.epoch == epoch and self.rt.state in (State.GENERATING, State.VALIDATING):
            self._go(State.LISTENING)

    async def _speak(self, text: str, epoch: int, trigger: str) -> None:
        if self.rt.state is not State.WAITING_TO_SPEAK or self.rt.epoch != epoch:
            return
        self._go(State.SPEAKING)
        uid = f"s_{trigger}"
        rec = {"id": uid, "text": text, "trigger": trigger, "delivered_ms": None, "outcome": "playing"}
        self.spoken.append(rec)
        self._recent_tts.append((text, self.clock()))
        self._emit(ev.SPEECH_STARTED, utterance=uid)
        await self.avatar.on_speech_start(uid, int(len(text) / 15 * 1000))
        eng = self

        async def chunks():
            async for ch in eng.tts.synthesize(text):
                if eng.rt.epoch != epoch:      # per-chunk staleness check
                    return
                await eng.avatar.on_audio(uid, ch, ch.ts_ms)
                yield ch

        try:
            receipt = await asyncio.wait_for(self.adapter.send_audio(uid, chunks()), self.p.max_speech_s)
        except asyncio.CancelledError:
            delivered = await self.adapter.stop_audio(uid)
            rec["delivered_ms"], rec["outcome"] = delivered, "interrupted"
            self._emit(ev.SPEECH_INTERRUPTED, utterance=uid, delivered_ms=delivered)
            await self.avatar.on_interrupt(uid, delivered)
            self._truncate_history(text, delivered)
            raise
        except (ProviderError, asyncio.TimeoutError, OSError):
            rec["outcome"] = "failed"
            rec["delivered_ms"] = await self.adapter.stop_audio(uid)   # never assume delivery
            self._emit(ev.SPEECH_FAILED, utterance=uid)
            if self.rt.state is State.SPEAKING:
                self._go(State.LISTENING)
            return
        rec["delivered_ms"] = receipt.delivered_ms
        if receipt.completed:
            rec["outcome"] = "completed"
            self.lines.append(TranscriptLine("Tara", 1.0, text))
            self._emit(ev.SPEECH_DELIVERED, utterance=uid, delivered_ms=receipt.delivered_ms)
        else:
            rec["outcome"] = "interrupted"
            self._truncate_history(text, receipt.delivered_ms)
        if self.rt.state is State.SPEAKING:
            self._go(State.LISTENING)

    def _truncate_history(self, text: str, delivered_ms: int, cps: float = 15.0) -> None:
        """History keeps only what was (approximately) heard. cps is a rough estimate."""
        heard = text[: int(delivered_ms / 1000 * cps)].rstrip()
        if heard:
            self.lines.append(TranscriptLine("Tara", 1.0, heard + " [interrupted]"))

    # ---- interruption / takeover --------------------------------------------------
    async def _cancel_task(self) -> None:
        t, self._task = self._task, None
        if t and not t.done() and t is not asyncio.current_task():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    async def _interrupt(self) -> None:
        self.rt.on_barge_in()              # SPEAKING -> INTERRUPTED, epoch++
        await self._cancel_task()
        self._emit(ev.STATE_CHANGED, state="INTERRUPTED")

    async def _make_stale(self) -> None:
        self.rt.bump_epoch()
        await self._cancel_task()
        if self.rt.state in (State.GENERATING, State.VALIDATING, State.WAITING_TO_SPEAK):
            self._go(State.LISTENING)
        self._emit(ev.PROPOSAL_REJECTED, reason="conversation_moved_on")

    async def _takeover(self) -> None:
        await self._cancel_task()
        self.rt.start_takeover()
        self._deferred = None
        self._emit(ev.TAKEOVER_STARTED)

    def release_takeover(self) -> None:
        self.rt.release_takeover()
        self._emit(ev.TAKEOVER_RELEASED)
