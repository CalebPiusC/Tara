"""Deterministic fakes for offline tests and simulations. Not for production."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable

from .contracts import AudioSegment, LlmRequest, PCMChunk, Transcript
from .errors import MalformedOutput, ProviderError, ProviderTimeout


class FakeLLMProvider:
    """responder(req, attempt) -> raw JSON-like value, or raises ProviderError."""

    def __init__(self, responder: Callable[[LlmRequest, int], Any], latency_s: float = 0.0, max_parse_retries: int = 1):
        self.responder, self.latency_s, self.max_parse_retries = responder, latency_s, max_parse_retries
        self.calls: list[LlmRequest] = []

    async def generate(self, req: LlmRequest, parse: Callable[[Any], Any], schema: dict | None = None) -> Any:
        self.calls.append(req)
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        last: Exception | None = None
        for attempt in range(self.max_parse_retries + 1):
            raw = self.responder(req, attempt)
            try:
                return parse(raw)
            except MalformedOutput as e:
                last = e
        raise MalformedOutput(str(last))


class FailingLLM:
    def __init__(self, exc: Exception | None = None):
        self.exc = exc or ProviderTimeout("fake timeout")
        self.calls = 0

    async def generate(self, req, parse, schema=None):
        self.calls += 1
        raise self.exc


class FakeStt:
    """Maps segment start_ms (or a FIFO queue) to text."""

    def __init__(self, by_start: dict[int, str] | None = None, queue: list[str] | None = None, fail_on: set[int] | None = None):
        self.by_start, self.queue, self.fail_on = by_start or {}, list(queue or []), fail_on or set()

    async def transcribe(self, seg: AudioSegment) -> Transcript:
        if seg.start_ms in self.fail_on:
            raise ProviderError("fake stt failure")
        text = self.by_start.get(seg.start_ms)
        if text is None and self.queue:
            text = self.queue.pop(0)
        return Transcript(text or "", seg.start_ms, seg.end_ms, 0.9)


class FakeTts:
    """~chars_per_second of speech as silent 24 kHz chunks, one sentence at a time."""

    sample_rate = 24000

    def __init__(self, chars_per_second: float = 15.0, chunk_ms: int = 100, chunk_delay_s: float = 0.0, fail: bool = False):
        self.cps, self.chunk_ms, self.delay, self.fail = chars_per_second, chunk_ms, chunk_delay_s, fail
        self.synthesized: list[str] = []

    def synthesize(self, text: str) -> AsyncIterator[PCMChunk]:
        async def gen() -> AsyncIterator[PCMChunk]:
            if self.fail:
                raise ProviderError("fake tts failure")
            self.synthesized.append(text)
            total_ms = int(len(text) / self.cps * 1000)
            t = 0
            while t < total_ms:
                n = min(self.chunk_ms, total_ms - t)
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield PCMChunk(b"\x00\x00" * (self.sample_rate * n // 1000), self.sample_rate, t, "tara")
                t += n
        return gen()
