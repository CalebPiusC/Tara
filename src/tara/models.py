"""Model lifecycle and inference resource control.

- ModelHandle: lazy, single-instance, failure-aware loading of one model.
- InferencePool: bounded concurrency + bounded wait queue + timeouts + abandonment
  of obsolete requests, so one slow inference cannot block the event loop.
- ModelManager: registry, health, idle unload, orderly shutdown.

Blocking work runs in threads. A thread cannot be killed: when a caller times out or is
cancelled, its result is discarded but the thread keeps its pool slot until it finishes,
so concurrency stays bounded (docs/RUNTIME_FOUNDATION.md, "Cancellation").
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar

from .errors import ModelLoadError, QueueFull
from .metrics import MODEL_LOAD, QUEUE_WAIT, Metrics

T = TypeVar("T")
R = TypeVar("R")


class ModelState(str, Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True)
class Health:
    name: str
    state: ModelState
    detail: str = ""
    load_ms: float | None = None
    last_error: str | None = None


class ModelHandle(Generic[T]):
    """One model, loaded at most once at a time. `loader` is blocking and runs in a thread."""

    def __init__(self, name: str, loader: Callable[[], T], closer: Callable[[T], None] | None = None,
                 metrics: Metrics | None = None, load_timeout_s: float = 120.0, retry_cooldown_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.name, self._loader, self._closer = name, loader, closer
        self.metrics = metrics or Metrics()
        self.load_timeout_s, self.retry_cooldown_s, self._clock = load_timeout_s, retry_cooldown_s, clock
        self.state = ModelState.UNLOADED
        self._model: T | None = None
        self._lock = asyncio.Lock()
        self._error: str | None = None
        self._failed_at = 0.0
        self._load_ms: float | None = None
        self.last_used = 0.0
        self.load_count = 0

    async def get(self) -> T:
        if self.state is ModelState.CLOSED:
            raise ModelLoadError(f"{self.name}: handle is closed")
        if self.state is ModelState.READY:
            self.last_used = self._clock()
            return self._model  # type: ignore[return-value]
        async with self._lock:                       # concurrent callers share one load
            if self.state is ModelState.READY:
                self.last_used = self._clock()
                return self._model  # type: ignore[return-value]
            if self.state is ModelState.CLOSED:
                raise ModelLoadError(f"{self.name}: handle is closed")
            if self.state is ModelState.FAILED and self._clock() - self._failed_at < self.retry_cooldown_s:
                raise ModelLoadError(f"{self.name}: load failed recently ({self._error}); retry after cooldown")
            self.state = ModelState.LOADING
            t0 = time.perf_counter()
            try:
                model = await asyncio.wait_for(asyncio.to_thread(self._loader), self.load_timeout_s)
            except asyncio.CancelledError:
                self.state = ModelState.UNLOADED
                raise
            except Exception as e:  # noqa: BLE001 - any loader failure becomes a structured error
                self.state, self._error, self._failed_at = ModelState.FAILED, f"{type(e).__name__}: {e}"[:200], self._clock()
                self.metrics.inc(f"model_load_failed.{self.name}")
                raise ModelLoadError(f"{self.name}: {self._error}") from e
            self._load_ms = (time.perf_counter() - t0) * 1000
            self.metrics.observe(MODEL_LOAD, self._load_ms)
            self._model, self.state, self._error = model, ModelState.READY, None
            self.load_count += 1
            self.last_used = self._clock()
            return model

    async def unload(self) -> None:
        async with self._lock:
            if self.state is ModelState.READY:
                await self._release()
                self.state = ModelState.UNLOADED

    async def close(self) -> None:
        async with self._lock:
            if self.state is ModelState.READY:
                await self._release()
            self.state = ModelState.CLOSED

    async def _release(self) -> None:
        model, self._model = self._model, None
        if self._closer is not None and model is not None:
            await asyncio.to_thread(self._closer, model)

    def health(self) -> Health:
        return Health(self.name, self.state, self._error or "", self._load_ms, self._error)


class InferencePool:
    """Bounded concurrency with a bounded wait queue."""

    def __init__(self, name: str, max_concurrent: int, max_queue: int, metrics: Metrics | None = None):
        if max_concurrent < 1 or max_queue < 0:
            raise ValueError("max_concurrent must be >= 1 and max_queue >= 0")
        self.name, self.max_queue = name, max_queue
        self.metrics = metrics or Metrics()
        self._sem = asyncio.Semaphore(max_concurrent)
        self.waiting = 0
        self.active = 0

    async def run(self, fn: Callable[..., R], *args: Any, timeout_s: float | None = None) -> R:
        if self.waiting >= self.max_queue and self._sem.locked():
            self.metrics.inc(f"queue_rejected.{self.name}")
            raise QueueFull(f"{self.name}: queue full ({self.max_queue})")
        self.waiting += 1
        self.metrics.gauge(f"queue_depth.{self.name}", self.waiting)
        t0 = time.perf_counter()
        try:
            await self._sem.acquire()
        except asyncio.CancelledError:
            self.metrics.inc(f"cancelled.{self.name}")
            raise
        finally:
            self.waiting -= 1
            self.metrics.gauge(f"queue_depth.{self.name}", self.waiting)
        self.metrics.observe(QUEUE_WAIT, (time.perf_counter() - t0) * 1000)
        self.active += 1
        self.metrics.gauge(f"active.{self.name}", self.active)
        task = asyncio.ensure_future(asyncio.to_thread(fn, *args))

        def _done(t: asyncio.Future) -> None:      # slot is held until the thread really finishes
            self.active -= 1
            self._sem.release()
            self.metrics.gauge(f"active.{self.name}", self.active)
            if not t.cancelled():
                t.exception()                       # mark retrieved: abandoned failures must not warn

        task.add_done_callback(_done)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout_s)
        except TimeoutError:
            self.metrics.inc(f"timeout.{self.name}")
            raise
        except asyncio.CancelledError:
            self.metrics.inc(f"cancelled.{self.name}")
            raise


class ModelManager:
    def __init__(self, metrics: Metrics | None = None, clock: Callable[[], float] = time.monotonic):
        self.metrics = metrics or Metrics()
        self._handles: dict[str, ModelHandle[Any]] = {}
        self._clock = clock

    def register(self, handle: ModelHandle[Any]) -> ModelHandle[Any]:
        if handle.name in self._handles:
            raise ValueError(f"duplicate model {handle.name!r}")   # prevents duplicate instances
        self._handles[handle.name] = handle
        return handle

    def get(self, name: str) -> ModelHandle[Any]:
        return self._handles[name]

    def health(self) -> dict[str, Health]:
        return {n: h.health() for n, h in self._handles.items()}

    async def warm_up(self, names: list[str] | None = None) -> dict[str, str | None]:
        """Optional eager load. Failures are returned, not raised, so startup can degrade."""
        out: dict[str, str | None] = {}
        for n in names or list(self._handles):
            try:
                await self._handles[n].get()
                out[n] = None
            except ModelLoadError as e:
                out[n] = str(e)
        return out

    async def unload_idle(self, idle_s: float) -> list[str]:
        if idle_s <= 0:
            return []
        now, done = self._clock(), []
        for n, h in self._handles.items():
            if h.state is ModelState.READY and now - h.last_used >= idle_s:
                await h.unload()
                done.append(n)
        return done

    async def shutdown(self) -> list[str]:
        """Close in reverse registration order; collect (don't hide) errors."""
        errors: list[str] = []
        for n in reversed(list(self._handles)):
            try:
                await self._handles[n].close()
            except Exception as e:  # noqa: BLE001 - shutdown must continue; errors are returned
                errors.append(f"{n}: {type(e).__name__}")
        return errors
