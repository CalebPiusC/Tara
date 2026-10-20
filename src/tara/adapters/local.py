"""LocalMeetingAdapter: scripted/in-memory meeting for development and tests.
Proves nothing about Google Meet (docs/MEETING_PLATFORM_INTEGRATION.md)."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..contracts import (
    ActionOutcome,
    AdapterCapabilities,
    DeliveryReceipt,
    MeetingEvent,
    MeetingEventKind,
    MeetingTarget,
    PCMChunk,
)
from ..errors import JoinError


class LocalMeetingAdapter:
    def __init__(self, per_participant_audio: bool = False, realtime_factor: float = 0.0, fail_join: bool = False,
                 controls: bool = False, fail_controls: bool = False):
        self.capabilities = AdapterCapabilities(per_participant_audio=per_participant_audio, delivery_confirmation=True,
                                                can_mute_self=controls, can_camera=controls, can_show_image=controls)
        self.calls: list[tuple[str, object]] = []      # recorded control calls (tests)
        self.fail_controls = fail_controls
        self._q: asyncio.Queue[MeetingEvent | None] = asyncio.Queue()
        self.realtime_factor = realtime_factor  # 1.0 = pace sends at real time; 0 = instant
        self.fail_join = fail_join
        self.joined = False
        self.sent: dict[str, int] = {}          # utterance_id -> delivered ms
        self.completed: dict[str, bool] = {}
        self._stop: set[str] = set()

    async def join(self, target: MeetingTarget) -> None:
        if self.fail_join:
            raise JoinError("scripted join failure")
        self.joined = True

    def push(self, ev: MeetingEvent) -> None:
        self._q.put_nowait(ev)

    def close(self) -> None:
        self._q.put_nowait(None)

    async def events(self) -> AsyncIterator[MeetingEvent]:
        while True:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev
            if ev.kind is MeetingEventKind.ENDED:
                return

    async def send_audio(self, utterance_id: str, chunks: AsyncIterator[PCMChunk]) -> DeliveryReceipt:
        delivered = 0
        self.sent[utterance_id] = 0
        self.completed[utterance_id] = False   # only True once fully played
        async for ch in chunks:
            if utterance_id in self._stop:
                return DeliveryReceipt(utterance_id, delivered, False)
            if self.realtime_factor:
                await asyncio.sleep(ch.duration_ms / 1000 * self.realtime_factor)
            delivered += ch.duration_ms
            self.sent[utterance_id] = delivered
        stopped = utterance_id in self._stop            # stop requested while the stream was ending
        self.completed[utterance_id] = not stopped
        return DeliveryReceipt(utterance_id, delivered, not stopped)

    async def stop_audio(self, utterance_id: str) -> int:
        self._stop.add(utterance_id)
        return self.sent.get(utterance_id, 0)

    async def leave(self) -> None:
        self.calls.append(("leave", None))
        self.joined = False

    async def _control(self, name: str, arg: object) -> ActionOutcome:
        self.calls.append((name, arg))
        return ActionOutcome(not self.fail_controls, "scripted failure" if self.fail_controls else "")

    async def set_microphone(self, on: bool) -> ActionOutcome:
        return await self._control("set_microphone", on)

    async def set_camera(self, on: bool) -> ActionOutcome:
        return await self._control("set_camera", on)

    async def show_image(self, path: str) -> ActionOutcome:
        return await self._control("show_image", path)
