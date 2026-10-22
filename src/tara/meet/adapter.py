"""MeetAdapter: the MeetingAdapter the Live voice engine already understands, backed by a Google Meet browser worker.

Meeting audio comes out of the page through the bridge and becomes ordinary `AUDIO` events; Tara's speech goes back in as the
microphone. Who is speaking: Meet gives one mixed stream, so for an interview with exactly ONE other person in the room the audio is
labelled with the candidate's stream id; with two or more people it is labelled `mix`, which the engine treats as an unknown speaker
(so identity-gated rules, such as answering only the candidate, stay quiet rather than guess). Nothing is recorded or kept.
"""
from __future__ import annotations

import array
import asyncio
import itertools
import json
import math
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from ..core.contracts import AdapterCapabilities, DeliveryReceipt, MeetingEvent, MeetingEventKind, MeetingTarget, PCMChunk
from ..core.errors import JoinError
from ..core.metrics import Metrics
from ..floor.audio import AdaptiveGain
from .bridge import IN_RATE, AudioBridge
from .controller import InterviewSession, MeetController
from .recorder import MeetingRecorder
from .worker import TERMINAL, Launcher, MeetWorker, State, WorkerConfig, WorkerEvent

MAX_STOPPED_IDS = 64
Status = Callable[[str], None]


def _pid(name: str) -> str:
    return "p-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "guest")


class MeetAdapter:
    def __init__(self, controller: MeetController, launcher_factory: Callable[[AudioBridge], Launcher], *, session_id: str,
                 interview_code: str = "", url: str = "", candidate_stream_id: str = "you", worker_cfg: WorkerConfig | None = None,
                 queue_chunks: int = 200, on_status: Status | None = None, metrics: Metrics | None = None,
                 on_first_participant: Callable[[], Awaitable[None]] | None = None, diag_s: float = 20.0,
                 warn_after_s: float = 30.0, log: Callable[[str], None] | None = None, audio: Any | None = None,
                 cables: dict[str, str] | None = None, half_duplex: bool = False, tail_s: float = 1.2,
                 on_people: Callable[[int], None] | None = None, picture: str = "", recorder: MeetingRecorder | None = None, gain: AdaptiveGain | None = None):
        self.capabilities = AdapterCapabilities(per_participant_audio=False, participant_events=True, send_audio=True,
                                                delivery_confirmation=True, can_mute_self=False, can_camera=False,
                                                can_show_image=False, can_mute_others=False)
        self.controller, self.launcher_factory = controller, launcher_factory
        self.session_id, self.interview_code, self.candidate_stream_id = session_id, interview_code, candidate_stream_id
        self.url = url                                  # the engine calls join() with no target; this is the meeting
        self.worker_cfg = worker_cfg or WorkerConfig()
        self.on_status, self.metrics = on_status or (lambda _m: None), metrics or Metrics()
        self.on_first_participant = on_first_participant
        self.diag_s = diag_s                          # how often to say what the audio route is doing (0 = never)
        self.warn_after_s = warn_after_s              # how long a dead audio route may be silent before a warning
        self._diag: asyncio.Task[None] | None = None
        self._heard = False
        self._tap_logged = False
        self.gain = gain                              # lifts quiet voices before the model hears them (the recording stays raw)
        self.recorder = recorder                      # opt-in audio recording (room + Tara), None = nothing is recorded
        self.on_people = on_people                    # told how many other people are in the room whenever that (debounced) number changes
        self.half_duplex, self.tail_s = half_duplex, tail_s   # while Tara speaks (and for a moment after), meeting audio is replaced by silence
        self._speaking = False
        self._quiet_until = 0.0
        self.silenced_frames = 0
        self.devices = audio                          # an AudioDeviceAdapter on the two virtual cables, or None for the in-page route
        self._forward: asyncio.Task[None] | None = None
        self._peak = 0.0
        self.log = log or (lambda _m: None)           # detail that goes to the run log file, not the screen
        self.bridge = AudioBridge(self._on_remote_audio, "devices" if audio is not None else "page", cables, picture)
        self.worker: MeetWorker | None = None
        self.session: InterviewSession | None = None
        self._queue: asyncio.Queue[MeetingEvent | None] = asyncio.Queue(maxsize=queue_chunks)
        self._pump: asyncio.Task[None] | None = None
        self._seq = itertools.count(1)
        self._t0 = time.monotonic()
        self.seen_people: dict[str, None] = {}        # everyone who was ever in the room (for the minutes), in order
        self._talk_log: list[tuple[float, frozenset[str]]] = []   # when the set of people talking changed (best effort, from the call screen)
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
        if self.devices is not None:
            self.devices.stream_id = self._stream_id()
            try:
                await self.devices.join(MeetingTarget())                  # missing cables fail here, before any browser opens
            except JoinError:
                self.controller.close_session(self.session_id, "failed", "audio devices unavailable")
                raise
            self._forward = asyncio.create_task(self._forward_device_audio(), name="meet-adapter-devices")
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
        if self.diag_s > 0:
            self._diag = asyncio.create_task(self._diagnose(), name="meet-adapter-diag")

    async def _abort(self, state: str, detail: str) -> None:
        await self._close_devices()
        if self.worker is not None:
            await self.worker.leave()
        self.controller.close_session(self.session_id, state, detail)
        self._finish_stream()

    async def _diagnose(self) -> None:
        """Say what the audio route is doing, because a silent bot is hard to debug. Warnings name the likely cause."""
        waited = 0.0
        last_wire = (0, 0)
        while True:
            await asyncio.sleep(self.diag_s)
            waited += self.diag_s
            page = self._page()
            if page is None:
                continue
            if self.devices is not None:
                try:
                    st = await AudioBridge.status(page)
                    self.log("debug " + json.dumps(await AudioBridge.debug(page), separators=(",", ":"))[:6000])
                    self.log("status " + json.dumps(st, separators=(",", ":"))[:2000])
                except Exception:  # noqa: BLE001
                    continue
                found = st.get("devices") or {}
                frames = int(getattr(self.devices, "input_chunks", 0))
                self.on_status(f"audio check (cables): frames from the meeting cable={frames}, loudest heard={self._peak:.3f}, "
                               f"Chrome microphone={found.get('mic') or 'NOT FOUND'}, Chrome speaker={found.get('speaker') or 'NOT FOUND'}, "
                               f"people seen={len(self._people)}")
                if waited >= self.warn_after_s:
                    if not found.get("mic") or not found.get("speaker"):
                        self.on_status("WARNING: Chrome cannot see one of the virtual cables, so Meet is not using it. Check the cable "
                                       "names (TARA_MEET_IN_DEVICE and TARA_MEET_OUT_DEVICE) and that the cables are installed.")
                    elif frames == 0:
                        self.on_status("WARNING: nothing is coming from the meeting cable (no audio frames at all).")
                    elif self._peak < 0.01:
                        self.on_status("WARNING: the meeting cable is silent: nobody is audibly speaking, or Meet is not playing into the cable.")
                continue
            try:
                st = await AudioBridge.status(page)
                wire = await AudioBridge.stats(page)
                self.log("debug " + json.dumps(await AudioBridge.debug(page), separators=(",", ":"))[:6000])
                self.log("status " + json.dumps(st, separators=(",", ":"))[:2000])
            except Exception:  # noqa: BLE001
                continue
            self.on_status(f"audio check: meeting audio tracks={st.get('remoteTracks', 0)}, frames received={self.bridge.frames_in}, "
                           f"level={float(st.get('inRms', 0)):.3f}, microphone opened by Meet={st.get('gumCalls', 0)}x, "
                           f"people seen={len(self._people)}")
            d_packets = int(wire.get("inPackets", 0)) - last_wire[0]
            last_wire = (int(wire.get("inPackets", 0)), int(wire.get("inBytes", 0)))
            peak = float(st.get("peakRms", 0))
            wire_peak = float(st.get("wirePeak", 0))
            if wire:
                self.on_status(f"on the wire: {d_packets} audio packets since the last check; loudest level Meet's own packets report="
                               f"{wire_peak:.3f} (0 means silent, 0.3 is clearly speech); loudest the tap has heard={peak:.3f}; "
                               f"Tara's microphone packets sent={wire.get('outPackets', 0)}")
            info = st.get("tapInfo") or []
            if info and not self._tap_logged:
                self._tap_logged = True
                self.on_status("tapped tracks: " + "; ".join(
                    f"#{i + 1} {'silent now' if t.get('muted') else 'live'}, {t.get('state')}, {'playing' if not t.get('paused') else 'NOT playing'}"
                    for i, t in enumerate(info)))
            if waited >= self.warn_after_s:
                if int(st.get("remoteTracks", 0)) == 0:
                    self.on_status("WARNING: no incoming audio track was captured, so Tara cannot hear the meeting (Meet may be using an "
                                   "audio route the bridge does not see).")
                elif wire_peak >= 0.02 and peak < 0.01:
                    self.on_status("WARNING: Meet's packets report audible speech but the bridge tap is silent. This is a bridge problem, "
                                   "not a meeting problem.")
                elif d_packets > 0 and wire_peak < 0.005 and peak < 0.01:
                    self.on_status("WARNING: Meet's packets report silence. Nobody is audibly speaking, or the speaker's microphone is muted "
                                   "in Meet, so there is nothing for Tara to hear (if Meet sends no level information this can read zero).")
                if int(st.get("gumCalls", 0)) == 0:
                    self.on_status("WARNING: Meet has not opened the microphone, so Tara's voice cannot reach the meeting.")

    async def _close_devices(self) -> None:
        if self._forward is not None and not self._forward.done():
            self._forward.cancel()
            await asyncio.gather(self._forward, return_exceptions=True)
        if self.devices is not None:
            try:
                await self.devices.leave()
            except Exception:  # noqa: BLE001 - leaving must not raise
                pass

    async def _forward_device_audio(self) -> None:
        """Meeting audio arrives from the virtual cable as ordinary audio events from the sound card adapter."""
        assert self.devices is not None
        async for ev in self.devices.events():
            if ev.kind is MeetingEventKind.AUDIO:
                chunk = ev.payload["chunk"]
                if self.recorder is not None:
                    self.recorder.write_room(chunk.data, chunk.sample_rate)
                gated = self._gate_echo(self.gain.process(chunk.data) if self.gain is not None else chunk.data)
                if gated is not chunk.data:
                    chunk = PCMChunk(gated, chunk.sample_rate, chunk.ts_ms, chunk.stream_id)
                self._measure(chunk.data)
                self._emit(MeetingEventKind.AUDIO, {"chunk": chunk})
                self.metrics.inc("meet_audio_frames_in")

    async def leave(self) -> None:
        self._closed = True
        await self._close_devices()
        if self._diag is not None and not self._diag.done():
            self._diag.cancel()
            await asyncio.gather(self._diag, return_exceptions=True)
        w = self.worker
        if w is not None:
            await w.leave()
        if self._pump is not None and not self._pump.done():
            self._pump.cancel()
            await asyncio.gather(self._pump, return_exceptions=True)
        self._joined = False
        if self.recorder is not None:
            self.recorder.close()
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
        """The label audio carries. In an interview it says who can be trusted as the candidate (exactly one other person, else `mix`).
        In a normal meeting it never changes: a label that flips with the people list leaves the old label marked as still talking."""
        if not self.interview_code:
            return self.candidate_stream_id
        return self.candidate_stream_id if len(self._people) == 1 else "mix"

    def _gate_echo(self, pcm: bytes) -> bytes:
        """Half-duplex: a participant's speaker and microphone can send Tara's own voice back a second later, which makes her answer herself
        and cut her own speech short. While she speaks, and for `tail_s` after, silence is passed on instead (the stream keeps flowing)."""
        if self.half_duplex and (self._speaking or time.monotonic() < self._quiet_until):
            self.silenced_frames += 1
            return b"\x00" * len(pcm)
        return pcm

    def _measure(self, pcm: bytes) -> None:
        samples = array.array("h")
        samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
        if not samples:
            return
        level = math.sqrt(sum(x * x for x in samples) / len(samples)) / 32768
        if level > self._peak:
            self._peak = level
        if not self._heard and level > 0.01:
            self._heard = True
            self.on_status("hearing the meeting: audio is coming through.")

    def _on_remote_audio(self, pcm: bytes) -> None:
        if self._closed:
            return
        ts = int((time.monotonic() - self._t0) * 1000)
        if self.recorder is not None:
            self.recorder.write_room(pcm, IN_RATE)                  # the raw room audio, before any echo silencing
        if self.gain is not None:
            pcm = self.gain.process(pcm)
        pcm = self._gate_echo(pcm)
        self._emit(MeetingEventKind.AUDIO, {"chunk": PCMChunk(pcm, IN_RATE, ts, self._stream_id())})
        self.metrics.inc("meet_audio_frames_in")
        self._measure(pcm)

    async def _pump_events(self) -> None:
        assert self.worker is not None
        while True:
            ev: WorkerEvent = await self.worker.events.get()
            if ev.kind == "speaking":
                self._talk_log.append((time.monotonic(), frozenset(ev.names)))
                del self._talk_log[:-300]
                self.log(f"speaking: {', '.join(ev.names) or 'nobody'}")
            elif ev.kind == "participants":
                await self._on_people(ev)
            elif ev.state is not None:
                self._on_state(ev)
                if ev.state in TERMINAL:
                    self._emit(MeetingEventKind.ENDED, {"reason": ev.state.value, "detail": ev.detail})
                    self._finish_stream(clear=False)
                    return

    def speaker_for_turn(self, window_s: float = 10.0, share: float = 0.6) -> str:
        """Who was talking over the last few seconds, from the call screen: one name when one person clearly held the floor, otherwise
        "" (unknown). Time-weighted, so a short interjection does not steal a long answer. A best guess, never a certainty."""
        now = time.monotonic()
        start = now - window_s
        weight: dict[str, float] = {}
        for i, (t, names) in enumerate(self._talk_log):
            end = self._talk_log[i + 1][0] if i + 1 < len(self._talk_log) else now
            lo, hi = max(t, start), min(end, now)
            if hi <= lo or not names:
                continue
            for n in names:
                weight[n] = weight.get(n, 0.0) + (hi - lo) / len(names)     # overlap splits the time between those talking
        total = sum(weight.values())
        if total <= 0.5:
            return ""
        name, w = max(weight.items(), key=lambda kv: kv[1])
        return name if w / total >= share else ""

    async def _on_people(self, ev: WorkerEvent) -> None:
        for name in ev.left:
            pid = next((p for p, n in self._people.items() if n == name), None)
            if pid is not None:
                del self._people[pid]
                self._emit(MeetingEventKind.PARTICIPANT_LEFT, {"id": pid})
        for name in ev.joined:
            self.seen_people.setdefault(name, None)
            pid = self.candidate_stream_id if not self._people and not self._announced else _pid(name)
            self._people[pid] = name
            self._emit(MeetingEventKind.PARTICIPANT_JOINED, {"id": pid, "name": name})
        if self.devices is not None:
            self.devices.stream_id = self._stream_id()
        if self.on_people is not None:
            try:
                self.on_people(len(self._people))
            except Exception:  # noqa: BLE001 - a policy hiccup must not break the audio path
                self.metrics.inc("meet_on_people_failed")
        self.on_status("people in the meeting: " + (", ".join(sorted(self._people.values())) or "nobody else yet"))
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
        self._speaking = True
        try:
            return await self._send_audio(utterance_id, self._tee(chunks))
        finally:
            self._speaking = False
            self._quiet_until = time.monotonic() + self.tail_s

    async def _tee(self, chunks: AsyncIterator[PCMChunk]) -> AsyncIterator[PCMChunk]:
        async for chunk in chunks:
            if self.recorder is not None:
                self.recorder.write_tara(chunk.data, chunk.sample_rate)
            yield chunk

    async def _send_audio(self, utterance_id: str, chunks: AsyncIterator[PCMChunk]) -> DeliveryReceipt:
        if self.devices is not None:
            if self._closed or not self._joined or self.worker is None or self.worker.state is not State.IN_MEETING:
                return DeliveryReceipt(utterance_id, 0, False)
            receipt = await self.devices.send_audio(utterance_id, chunks)
            if receipt.completed:
                self.on_status(f"Tara spoke for {receipt.delivered_ms / 1000:.1f}s")
            return receipt
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
                    self.on_status(f"Tara spoke for {total_ms / 1000:.1f}s")
                    return DeliveryReceipt(utterance_id, total_ms, True)
                await asyncio.sleep(0.05)
            return DeliveryReceipt(utterance_id, total_ms, True)
        finally:
            if self._play_uid == utterance_id:
                self._play_uid = None

    async def stop_audio(self, utterance_id: str) -> int:
        if self.devices is not None:
            return await self.devices.stop_audio(utterance_id)
        self._stopped.append(utterance_id)
        page = self._page()
        if page is None or self._play_uid != utterance_id:
            return 0
        try:
            return await AudioBridge.stop(page)
        except Exception:  # noqa: BLE001 - cancelling must not raise
            return 0
