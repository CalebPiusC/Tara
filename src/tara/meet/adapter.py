"""MeetAdapter: the MeetingAdapter the Live voice engine already understands, backed by a Google Meet browser worker.

Meeting audio comes out of the page through the bridge and becomes ordinary `AUDIO` events; Tara's speech goes back in as the
microphone. Who is speaking: Meet gives one mixed stream, so for an interview with exactly ONE other person in the room the audio is
labelled with the candidate's stream id; with two or more people it is labelled `mix`, which the engine treats as an unknown speaker
(so identity-gated rules, such as answering only the candidate, stay quiet rather than guess). Nothing is recorded or kept.
"""
from __future__ import annotations

import asyncio
import itertools
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from ..contracts import AdapterCapabilities, DeliveryReceipt, MeetingEvent, MeetingEventKind, MeetingTarget, PCMChunk
from ..errors import JoinError
from ..metrics import Metrics
from .bridge import IN_RATE, AudioBridge
from .controller import InterviewSession, MeetController
from .worker import TERMINAL, Launcher, MeetWorker, State, WorkerConfig, WorkerEvent

MAX_STOPPED_IDS = 64
Status = Callable[[str], None]


def _pid(name: str) -> str:
    return "p-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "guest")


class MeetAdapter:
    def __init__(self, controller: MeetController, launcher_factory: Callable[[AudioBridge], Launcher], *, session_id: str,
                 interview_code: str = "", url: str = "", candidate_stream_id: str = "you", worker_cfg: WorkerConfig | None = None,
                 queue_chunks: int = 200, on_status: Status | None = None, metrics: Metrics | None = None,
                 on_first_participant: Callable[[], Awaitable[None]] | None = None):
        self.capabilities = AdapterCapabilities(per_participant_audio=False, participant_events=True, send_audio=True,
                                                delivery_confirmation=True, can_mute_self=False, can_camera=False,
                                                can_show_image=False, can_mute_others=False)
        self.controller, self.launcher_factory = controller, launcher_factory
        self.session_id, self.interview_code, self.candidate_stream_id = session_id, interview_code, candidate_stream_id
        self.url = url                                  # the engine calls join() with no target; this is the meeting
        self.worker_cfg = worker_cfg or WorkerConfig()
        self.on_status, self.metrics = on_status or (lambda _m: None), metrics or Metrics()
        self.on_first_participant = on_first_participant
        self.bridge = AudioBridge(self._on_remote_audio)
        self.worker: MeetWorker | None = None
        self.session: InterviewSession | None = None
        self._queue: asyncio.Queue[MeetingEvent | None] = asyncio.Queue(maxsize=queue_chunks)
        self._pump: asyncio.Task[None] | None = None
        self._seq = itertools.count(1)
        self._t0 = time.monotonic()
        self._people: dict[str, str] = {}              # participant id -> name
        self._announced = False
        self._joined = False
        self._closed = False
        self._stopped: deque[str] = deque(maxlen=MAX_STOPPED_IDS)
        self._play_uid: str | None = None
        self.dropped_chunks = 0
        self.frames_out = 0

    # ---- lifecycle ---------------------------------------------------------------------------------------------
    async def join(self, target: MeetingTarget | None = None) -> None:
        if self._joined:
            return
        url = (target.url if target else "") or self.url
        self.session = self.controller.open_session(self.session_id, self.interview_code, url)        # raises on duplicates
        self._closed = False
        self._t0 = time.monotonic()
        self.worker = MeetWorker(self.session.url, self.launcher_factory(self.bridge), self.worker_cfg)
        self.worker.start()
        self._pump = asyncio.create_task(self._pump_events(), name="meet-adapter-pump")
        budget = self.worker_cfg.admit_timeout_s + self.worker_cfg.join_timeout_s + 30
        try:
            state = await self.worker.wait_for(State.IN_MEETING, timeout=budget)
        except TimeoutError as exc:
            await self._abort("timeout", str(exc))
            raise JoinError(f"did not get into the meeting: {exc}") from exc
        if state is not State.IN_MEETING and not self.worker.ever_in_meeting:
            detail = self.worker.detail
            await self._abort(state.value, detail)
            raise JoinError(f"could not join the meeting ({state.value}): {detail}")
        self._joined = True
        self.on_status("In the meeting.")

    async def _abort(self, state: str, detail: str) -> None:
        if self.worker is not None:
            await self.worker.leave()
        self.controller.close_session(self.session_id, state, detail)
        self._finish_stream()

    async def leave(self) -> None:
        self._closed = True
        w = self.worker
        if w is not None:
            await w.leave()
        if self._pump is not None and not self._pump.done():
            self._pump.cancel()
            await asyncio.gather(self._pump, return_exceptions=True)
        self._joined = False
        self.controller.close_session(self.session_id, "ended")
        self._finish_stream()

    def _finish_stream(self, *, clear: bool = True) -> None:
        """End the event stream. On shutdown stale frames are dropped; when the meeting itself ended, the ENDED event is kept."""
        while clear and not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                break
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
        self._queue.put_nowait(None)

    async def events(self) -> AsyncIterator[MeetingEvent]:
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    # ---- worker events -> meeting events ----------------------------------------------------------------------------
    def _emit(self, kind: MeetingEventKind, payload: dict[str, Any] | None = None) -> None:
        if self._closed:
            return
        ts = int((time.monotonic() - self._t0) * 1000)
        ev = MeetingEvent(kind, ts, f"meet-{next(self._seq)}", payload or {})
        if self._queue.full():
            try:
                self._queue.get_nowait()                    # lagging audio is worse than a gap
                self.dropped_chunks += 1
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
        self._queue.put_nowait(ev)

    def _remote_ids(self) -> list[str]:
        return sorted(self._people)

    def _stream_id(self) -> str:
        return self.candidate_stream_id if len(self._people) == 1 else "mix"

    def _on_remote_audio(self, pcm: bytes) -> None:
        if self._closed:
            return
        ts = int((time.monotonic() - self._t0) * 1000)
        self._emit(MeetingEventKind.AUDIO, {"chunk": PCMChunk(pcm, IN_RATE, ts, self._stream_id())})
        self.metrics.inc("meet_audio_frames_in")

    async def _pump_events(self) -> None:
        assert self.worker is not None
        while True:
            ev: WorkerEvent = await self.worker.events.get()
            if ev.kind == "participants":
                await self._on_people(ev)
            elif ev.state is not None:
                self._on_state(ev)
                if ev.state in TERMINAL:
                    self._emit(MeetingEventKind.ENDED, {"reason": ev.state.value, "detail": ev.detail})
                    self._finish_stream(clear=False)
                    return

    async def _on_people(self, ev: WorkerEvent) -> None:
        for name in ev.left:
            pid = next((p for p, n in self._people.items() if n == name), None)
            if pid is not None:
                del self._people[pid]
                self._emit(MeetingEventKind.PARTICIPANT_LEFT, {"id": pid})
        for name in ev.joined:
            pid = self.candidate_stream_id if not self._people and not self._announced else _pid(name)
            self._people[pid] = name
            self._emit(MeetingEventKind.PARTICIPANT_JOINED, {"id": pid, "name": name})
        if len(self._people) == 1:
            self.on_status("One participant is in the meeting: audio is attributed to the candidate.")
        elif len(self._people) > 1:
            self.on_status(f"{len(self._people)} participants: speakers cannot be told apart, so Tara will not run the interview.")
        if self._people and not self._announced:
            self._announced = True
            if self.on_first_participant is not None:
                try:
                    await self.on_first_participant()
                except Exception:  # noqa: BLE001 - a missed greeting must not break the audio path
                    self.metrics.inc("meet_first_participant_failed")

    def _on_state(self, ev: WorkerEvent) -> None:
        assert ev.state is not None
        if self.session is not None:
            self.session.state, self.session.detail = ev.state.value, ev.detail
        self.on_status(f"{ev.state.value}" + (f": {ev.detail}" if ev.detail else ""))
        if ev.state is State.IN_MEETING and ev.reconnected:
            self.metrics.inc("meet_reconnects")
            self._emit(MeetingEventKind.RECONNECTED)

    # ---- Tara's voice into the meeting -----------------------------------------------------------------------------
    def _page(self) -> Any | None:
        w = self.worker
        if w is None or w.state is not State.IN_MEETING or w.page is None or w.page.is_closed():
            return None
        return getattr(w.page, "raw", w.page)

    async def send_audio(self, utterance_id: str, chunks: AsyncIterator[PCMChunk]) -> DeliveryReceipt:
        page = self._page()
        if self._closed or not self._joined or page is None or utterance_id in self._stopped:
            return DeliveryReceipt(utterance_id, 0, False)
        self._play_uid = utterance_id
        total_ms = 0
        try:
            async for chunk in chunks:
                if utterance_id in self._stopped or self._closed:
                    return DeliveryReceipt(utterance_id, 0, False)
                if not chunk.data:
                    continue
                page = self._page()
                if page is None:                                  # reconnecting: speech in flight is never assumed delivered
                    return DeliveryReceipt(utterance_id, total_ms, False)
                try:
                    await AudioBridge.play(page, chunk.data, chunk.sample_rate)
                except Exception:  # noqa: BLE001 - a failed write ends this response only
                    self.metrics.inc("meet_play_failures")
                    return DeliveryReceipt(utterance_id, total_ms, False)
                total_ms += chunk.duration_ms
                self.frames_out += 1
            # all chunks are queued in the page; wait for them to be heard
            deadline = time.monotonic() + total_ms / 1000 + 3
            while time.monotonic() < deadline:
                if utterance_id in self._stopped or self._closed:
                    return DeliveryReceipt(utterance_id, 0, False)
                page = self._page()
                if page is None:
                    return DeliveryReceipt(utterance_id, total_ms, False)
                try:
                    st = await AudioBridge.status(page)
                except Exception:  # noqa: BLE001
                    return DeliveryReceipt(utterance_id, total_ms, False)
                if int(st.get("queuedMs", 0)) <= 0:
                    return DeliveryReceipt(utterance_id, total_ms, True)
                await asyncio.sleep(0.05)
            return DeliveryReceipt(utterance_id, total_ms, True)
        finally:
            if self._play_uid == utterance_id:
                self._play_uid = None

    async def stop_audio(self, utterance_id: str) -> int:
        self._stopped.append(utterance_id)
        page = self._page()
        if page is None or self._play_uid != utterance_id:
            return 0
        try:
            return await AudioBridge.stop(page)
        except Exception:  # noqa: BLE001 - cancelling must not raise
            return 0
