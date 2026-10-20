"""Scripted voice provider/session for tests and offline simulation. Not for production."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from ..errors import FatalProviderError, ProviderError
from .events import VoiceEvent, VoiceSessionConfig


class FakeVoiceSession:
    def __init__(self) -> None:
        self.audio: list[bytes] = []
        self.texts: list[str] = []
        self.contexts: list[str] = []
        self.tool_results: list[tuple[str, str, dict[str, Any]]] = []
        self.stream_ends = 0
        self.closed = False
        self.send_gate: asyncio.Event | None = None      # set to block send_audio (backpressure tests)
        self.fail_send = False
        self._q: asyncio.Queue[VoiceEvent | None] = asyncio.Queue()

    def push(self, *events: VoiceEvent) -> None:
        for e in events:
            self._q.put_nowait(e)

    def end(self) -> None:
        self._q.put_nowait(None)

    async def send_audio(self, pcm16_16k: bytes) -> None:
        if self.send_gate is not None:
            await self.send_gate.wait()
        if self.fail_send:
            raise ConnectionError("scripted send failure")
        self.audio.append(pcm16_16k)

    async def end_audio_stream(self) -> None:
        self.stream_ends += 1

    async def send_text(self, text: str) -> None:
        self.texts.append(text)

    async def send_context(self, text: str) -> None:
        self.contexts.append(text)

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        self.tool_results.append((call_id, name, result))

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        self.closed = True
        self._q.put_nowait(None)


class FakeVoiceProvider:
    """`open` hands out pre-made sessions in order; `failures` is consumed first (exceptions raised)."""

    def __init__(self, sessions: list[FakeVoiceSession] | None = None, failures: list[Exception] | None = None):
        self.sessions = list(sessions or [])
        self.failures = list(failures or [])
        self.opened: list[tuple[VoiceSessionConfig, str | None]] = []

    async def open(self, config: VoiceSessionConfig, resume_handle: str | None = None) -> FakeVoiceSession:
        self.opened.append((config, resume_handle))
        if self.failures:
            raise self.failures.pop(0)
        if not self.sessions:
            raise ProviderError("no scripted session left")
        return self.sessions.pop(0)


__all__ = ["FakeVoiceProvider", "FakeVoiceSession", "FatalProviderError"]
