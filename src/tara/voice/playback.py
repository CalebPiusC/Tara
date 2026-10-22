"""Playback controller: the application's side of interruption.

The provider reports interruption (`interrupted`); WE must stop what is audibly
playing. This module owns: a bounded queue per response, per-chunk staleness
checks, immediate cancellation, and delivery accounting through the meeting
adapter. Cancelling playback never touches the provider session.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from ..core.contracts import MeetingAdapter, PCMChunk
from ..core.metrics import Metrics

_END = object()


@dataclass
class PlaybackResult:
    response_id: int
    delivered_ms: int
    completed: bool
    cancelled: bool


class _Stream:
    def __init__(self, response_id: int, maxsize: int):
        self.response_id = response_id
        self.q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.cancelled = False
        self.ended = False
        self.task: asyncio.Task | None = None
        self.result: PlaybackResult | None = None
        self.started_at = 0.0
        self.first_sink_at: float | None = None
        self.offset_ms = 0


class PlaybackController:
    def __init__(self, adapter: MeetingAdapter, metrics: Metrics | None = None, max_queue_chunks: int = 64,
                 clock: Callable[[], float] = time.monotonic, stop_timeout_s: float = 2.0):
        self.adapter, self.metrics, self.max_queue = adapter, metrics or Metrics(), max_queue_chunks
        self._clock, self.stop_timeout_s = clock, stop_timeout_s
        self._active: _Stream | None = None
        self._cancelled_through = 0          # every response id <= this is dead
        self.results: list[PlaybackResult] = []
        self.on_finished: Callable[[PlaybackResult], None] | None = None   # set by the engine (never raises into playback)

    # ---- state ------------------------------------------------------------------
    @property
    def active_response(self) -> int | None:
        s = self._active
        return s.response_id if s and not s.cancelled and not (s.task and s.task.done()) else None

    @property
    def is_playing(self) -> bool:
        return self.active_response is not None

    def is_stale(self, response_id: int) -> bool:
        return response_id <= self._cancelled_through

    # ---- feed -------------------------------------------------------------------
    def enqueue(self, response_id: int, chunk: PCMChunk) -> bool:
        """Non-blocking. Returns False if the chunk was rejected (stale, cancelled, or dropped)."""
        if self.is_stale(response_id):
            self.metrics.inc("stale_chunks_dropped")
            return False
        s = self._active
        if s is None or s.response_id != response_id:
            if s is not None and s.response_id > response_id:
                self.metrics.inc("stale_chunks_dropped")
                return False
            s = self._start(response_id)
        if s.ended or s.cancelled:
            self.metrics.inc("stale_chunks_dropped")
            return False
        if s.q.full():
            try:
                s.q.get_nowait()                    # drop oldest: bounded memory beats unbounded lag
                self.metrics.inc("playback_chunks_dropped")
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
        s.q.put_nowait(chunk)
        self.metrics.gauge("playback_queue_depth", s.q.qsize())
        return True

    def finish(self, response_id: int) -> None:
        """No more audio for this response; let the queue drain."""
        s = self._active
        if s is not None and s.response_id == response_id and not s.ended:
            s.ended = True
            self._put_end(s)

    def _put_end(self, s: _Stream) -> None:
        if s.q.full():
            try:
                s.q.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
        s.q.put_nowait(_END)

    def _start(self, response_id: int) -> _Stream:
        s = _Stream(response_id, self.max_queue)
        s.started_at = self._clock()
        self._active = s
        s.task = asyncio.create_task(self._play(s))
        return s

    async def _iter(self, s: _Stream) -> AsyncIterator[PCMChunk]:
        while True:
            item = await s.q.get()
            if item is _END or s.cancelled:
                return
            if s.first_sink_at is None:
                s.first_sink_at = self._clock()
                self.metrics.observe("live_first_playback_ms", (s.first_sink_at - s.started_at) * 1000)
            s.offset_ms += item.duration_ms
            yield item

    async def _play(self, s: _Stream) -> None:
        uid = f"live_{s.response_id}"
        try:
            receipt = await self.adapter.send_audio(uid, self._iter(s))
            s.result = PlaybackResult(s.response_id, receipt.delivered_ms, receipt.completed and not s.cancelled, s.cancelled)
        except asyncio.CancelledError:
            s.result = PlaybackResult(s.response_id, s.offset_ms, False, True)
            raise
        except Exception:  # noqa: BLE001 - adapter failure: never assume delivery
            self.metrics.inc("playback_errors")
            s.result = PlaybackResult(s.response_id, s.offset_ms, False, s.cancelled)
        finally:
            final = s.result or PlaybackResult(s.response_id, s.offset_ms, False, s.cancelled)
            self.results.append(final)
            if self.on_finished is not None:
                try:
                    self.on_finished(final)
                except Exception:  # noqa: BLE001 - observers must not disturb playback
                    self.metrics.inc("playback_observer_errors")
            if self._active is s:
                self._active = None
            self.metrics.gauge("playback_queue_depth", 0)

    # ---- cancel -------------------------------------------------------------------
    async def cancel(self, reason: str, up_to: int | None = None) -> int | None:
        """Stop audible playback NOW and kill every id <= `up_to` (default: the active response).
        Returns delivered_ms of the cancelled response (None if nothing was playing)."""
        s = self._active
        t0 = self._clock()
        if up_to is not None:
            self._cancelled_through = max(self._cancelled_through, up_to)
        if s is None or s.cancelled:
            return None
        self._cancelled_through = max(self._cancelled_through, s.response_id)
        s.cancelled = True
        # 1) stop the sink first, so the adapter records a stop (not a normal end of stream)
        delivered = s.offset_ms
        try:
            delivered = await asyncio.wait_for(self.adapter.stop_audio(f"live_{s.response_id}"), self.stop_timeout_s)
        except TimeoutError:
            self.metrics.inc("playback_stop_timeouts")
        except Exception:  # noqa: BLE001 - a broken sink must not block cancellation
            self.metrics.inc("playback_errors")
        # 2) discard queued audio from the dead response and end its stream
        while not s.q.empty():
            try:
                s.q.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                break
        s.q.put_nowait(_END)
        if s.task is not None:
            done, _ = await asyncio.wait({s.task}, timeout=self.stop_timeout_s)   # propagates OUR cancellation
            if not done:
                s.task.cancel()
                self.metrics.inc("playback_stop_timeouts")
        self.metrics.inc(f"playback_cancelled.{reason}")
        self.metrics.observe("live_interrupt_stop_ms", (self._clock() - t0) * 1000)
        return delivered

    async def close(self) -> None:
        await self.cancel("close")
