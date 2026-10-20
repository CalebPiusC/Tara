"""Lightweight observability: counters, gauges, latency histograms (bounded),
and a structured-log helper that refuses to log meeting content."""
from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

# Stage names used across the runtime (docs/RUNTIME_FOUNDATION.md)
AUDIO, STT, ROUTE, RETRIEVAL, LOCAL_LLM, CLOUD_LLM, TTS_FIRST, QUEUE_WAIT, MODEL_LOAD, TOTAL = (
    "audio_ms", "stt_ms", "route_ms", "retrieval_ms", "local_llm_ms", "cloud_llm_ms",
    "tts_first_chunk_ms", "queue_wait_ms", "model_load_ms", "response_total_ms")

SENSITIVE_KEYS = frozenset({"text", "transcript", "audio", "prompt", "content", "question", "answer", "api_key",
                            "query", "passage", "body", "say"})


class Metrics:
    def __init__(self, clock: Callable[[], float] = time.perf_counter, hist_limit: int = 1000):
        self._clock = clock
        self.counters: Counter[str] = Counter()
        self.gauges: dict[str, float] = {}
        self._hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=hist_limit))

    def inc(self, name: str, n: int = 1) -> None:
        self.counters[name] += n

    def gauge(self, name: str, value: float) -> None:
        self.gauges[name] = value

    def observe(self, name: str, ms: float) -> None:
        self._hist[name].append(ms)

    @contextmanager
    def time(self, name: str) -> Iterator[None]:
        t0 = self._clock()
        try:
            yield
        finally:
            self.observe(name, (self._clock() - t0) * 1000)

    def summary(self, name: str) -> dict[str, float]:
        v = sorted(self._hist.get(name, ()))
        if not v:
            return {"count": 0}
        def pct(p: float) -> float:
            return v[min(len(v) - 1, int(p * len(v)))]
        return {"count": len(v), "p50": pct(0.5), "p95": pct(0.95), "max": v[-1]}

    def snapshot(self) -> dict[str, Any]:
        return {"counters": dict(self.counters), "gauges": dict(self.gauges),
                "latency": {k: self.summary(k) for k in self._hist}}


def safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Replace content-bearing values with their length. Keys are matched exactly."""
    out = {}
    for k, v in fields.items():
        out[k] = f"<redacted:{len(v)}>" if k in SENSITIVE_KEYS and hasattr(v, "__len__") else v
    return out


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    if logger.isEnabledFor(level):
        logger.log(level, json.dumps({"event": event, **safe_fields(fields)}, default=str, sort_keys=True))
