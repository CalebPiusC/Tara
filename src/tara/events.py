"""In-process event bus. Bounded queues; slow subscribers lose oldest events
(counted), never block the pipeline. Event names mirror docs and the live-voice
reference (vad.speech_started, response.cancelled, ...)."""
from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Any

# Stable event names (docs/API_AND_EVENT_CONTRACTS.md)
VAD_STARTED = "vad.speech_started"
VAD_ENDED = "vad.speech_ended"
TRANSCRIPT_FINAL = "transcript.final"
ATTRIBUTION = "attribution.updated"
PARTICIPATION = "participation.decided"
PROPOSAL_CREATED = "proposal.created"
PROPOSAL_APPROVED = "proposal.approved"
PROPOSAL_REJECTED = "proposal.rejected"
SPEECH_STARTED = "speech.playback_started"
SPEECH_DELIVERED = "speech.delivered"
SPEECH_INTERRUPTED = "speech.interrupted"
SPEECH_FAILED = "speech.failed"
TAKEOVER_STARTED = "takeover.started"
TAKEOVER_RELEASED = "takeover.released"
STATE_CHANGED = "runtime.state_changed"
TOOL_REQUESTED = "tool.requested"
TOOL_DENIED = "tool.denied"
TOOL_EXECUTED = "tool.executed"
REPORT_READY = "report.ready"

_ids = itertools.count(1)


@dataclass(frozen=True)
class Event:
    name: str
    ts_ms: int
    epoch: int
    data: dict[str, Any] = field(default_factory=dict)
    event_id: int = field(default_factory=lambda: next(_ids))


class Subscription:
    def __init__(self, maxsize: int):
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def offer(self, ev: Event) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - raced
                pass
            self.dropped += 1
        self.queue.put_nowait(ev)

    async def get(self) -> Event:
        return await self.queue.get()


class EventBus:
    def __init__(self, default_maxsize: int = 256):
        self._subs: list[tuple[str, Subscription]] = []
        self._max = default_maxsize
        self.history: list[Event] = []
        self.history_limit = 2048

    def subscribe(self, prefix: str = "", maxsize: int | None = None) -> Subscription:
        sub = Subscription(maxsize or self._max)
        self._subs.append((prefix, sub))
        return sub

    def publish(self, ev: Event) -> None:
        self.history.append(ev)
        if len(self.history) > self.history_limit:
            del self.history[: len(self.history) - self.history_limit]
        for prefix, sub in self._subs:
            if ev.name.startswith(prefix):
                sub.offer(ev)

    def names(self) -> list[str]:
        return [e.name for e in self.history]
