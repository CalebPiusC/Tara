"""Gemini Live adapter (server-mediated, via the google-genai SDK).

SDK surface verified 2026-10-20 by reading the official docs and inspecting
google-genai 2.31.0 locally (no network): `client.aio.live.connect(model, config)`,
`send_realtime_input(audio=Blob(...), audio_stream_end=...)`, `send_client_content`,
`send_tool_response(function_responses=[FunctionResponse(id, name, response)])`,
`receive()` which yields one model turn at a time and is therefore called in a loop.

NOT verified against the live service (no API key available when this was written):
connection acceptance for a given model id, event ordering between input
transcription and audio, and real latencies. Everything provider-specific stays in
this file; the mapper works on duck-typed messages so it is unit-testable.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from typing import Any

from ..errors import FatalProviderError, ProviderError, ProviderTimeout
from .events import (
    Disconnected,
    GenerationComplete,
    GoAway,
    InputTranscript,
    Interrupted,
    OutputTranscript,
    ResponseAudio,
    ResumptionHandle,
    SessionReady,
    ToolCallRequested,
    ToolCallsCancelled,
    ToolRequest,
    TurnComplete,
    VoiceEvent,
    VoiceSessionConfig,
)

INPUT_MIME = "audio/pcm;rate=16000"
DEFAULT_OUT_RATE = 24000


def redact(text: str, secrets: tuple[str, ...]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "[REDACTED]")
    return text


def safe_error(exc: BaseException, secrets: tuple[str, ...]) -> str:
    """Class name + code only, secrets removed, bounded length. Never the raw message body."""
    code = getattr(exc, "code", None) or getattr(getattr(exc, "rcvd", None), "code", None)
    msg = redact(str(exc), secrets)[:120]
    return f"{type(exc).__name__}{f' ({code})' if code else ''}: {msg}"


def _rate_from_mime(mime: str | None) -> int:
    if mime and "rate=" in mime:
        try:
            return int(mime.split("rate=")[1].split(";")[0])
        except ValueError:
            pass
    return DEFAULT_OUT_RATE


class ResponseTracker:
    """Our own response numbering. A new id starts at the first model audio after a
    turn boundary (turn complete or interruption)."""

    def __init__(self) -> None:
        self.current = 0
        self._open = False

    def audio(self) -> int:
        if not self._open:
            self.current += 1
            self._open = True
        return self.current

    def boundary(self) -> int:
        rid = self.current
        self._open = False
        return rid


def map_message(msg: Any, tracker: ResponseTracker) -> list[VoiceEvent]:
    """Translate one provider message into neutral events. Pure; duck-typed on attributes."""
    out: list[VoiceEvent] = []
    if getattr(msg, "setup_complete", None) is not None:
        out.append(SessionReady())
    sc = getattr(msg, "server_content", None)
    if sc is not None:
        # input transcription first: a participant speaking is relevant before any audio is handled
        it = getattr(sc, "input_transcription", None)
        if it is not None and getattr(it, "text", None):
            out.append(InputTranscript(it.text, bool(getattr(it, "finished", False))))
        mt = getattr(sc, "model_turn", None)
        for part in (getattr(mt, "parts", None) or []):
            blob = getattr(part, "inline_data", None)
            if blob is not None and getattr(blob, "data", None):
                out.append(ResponseAudio(tracker.audio(), bytes(blob.data), _rate_from_mime(getattr(blob, "mime_type", None))))
        ot = getattr(sc, "output_transcription", None)
        if ot is not None and getattr(ot, "text", None):
            out.append(OutputTranscript(ot.text, tracker.current, bool(getattr(ot, "finished", False))))
        if getattr(sc, "interrupted", None) is True:
            out.append(Interrupted(tracker.boundary()))
        if getattr(sc, "generation_complete", None) is True:
            out.append(GenerationComplete(tracker.current))
        if getattr(sc, "turn_complete", None) is True:
            reason = getattr(sc, "turn_complete_reason", None)
            out.append(TurnComplete(tracker.boundary(), getattr(reason, "name", str(reason)) if reason else ""))
    tc = getattr(msg, "tool_call", None)
    if tc is not None and getattr(tc, "function_calls", None):
        out.append(ToolCallRequested(tuple(
            ToolRequest(str(c.id), str(c.name), dict(c.args or {})) for c in tc.function_calls)))
    tcc = getattr(msg, "tool_call_cancellation", None)
    if tcc is not None and getattr(tcc, "ids", None):
        out.append(ToolCallsCancelled(tuple(str(i) for i in tcc.ids)))
    ga = getattr(msg, "go_away", None)
    if ga is not None:
        tl = getattr(ga, "time_left", None)
        out.append(GoAway(_seconds(tl)))
    sr = getattr(msg, "session_resumption_update", None)
    if sr is not None and getattr(sr, "new_handle", None):
        out.append(ResumptionHandle(sr.new_handle, bool(getattr(sr, "resumable", True))))
    return out


def _seconds(v: Any) -> float | None:
    if v is None:
        return None
    if hasattr(v, "total_seconds"):
        return float(v.total_seconds())
    try:
        return float(str(v).rstrip("s"))
    except ValueError:
        return None


def build_live_config(cfg: VoiceSessionConfig, resume_handle: str | None, types: Any) -> Any:
    """Build a LiveConnectConfig. `types` is google.genai.types (injected so tests can fake it)."""
    kw: dict[str, Any] = {
        "response_modalities": ["AUDIO"],
        "system_instruction": cfg.system_instruction,
    }
    if cfg.voice_name:
        kw["speech_config"] = types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=cfg.voice_name)))
    if cfg.input_transcription:
        kw["input_audio_transcription"] = types.AudioTranscriptionConfig()
    if cfg.output_transcription:
        kw["output_audio_transcription"] = types.AudioTranscriptionConfig()
    aad: dict[str, Any] = {}
    if cfg.silence_duration_ms is not None:
        aad["silence_duration_ms"] = cfg.silence_duration_ms
    handling = (types.ActivityHandling.NO_INTERRUPTION if cfg.activity_handling == "none"
                else types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS)
    kw["realtime_input_config"] = types.RealtimeInputConfig(
        activity_handling=handling,
        **({"automatic_activity_detection": types.AutomaticActivityDetection(**aad)} if aad else {}))
    if cfg.resumption or resume_handle:
        kw["session_resumption"] = types.SessionResumptionConfig(handle=resume_handle)
    if cfg.context_compression:
        kw["context_window_compression"] = types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow())
    if cfg.proactive_audio:
        kw["proactivity"] = types.ProactivityConfig(proactive_audio=True)
    if cfg.tools:
        beh = {"non_blocking": types.Behavior.NON_BLOCKING, "blocking": types.Behavior.BLOCKING}.get(cfg.tool_behavior)
        decls = [types.FunctionDeclaration(name=t.name, description=t.description,
                                           parameters_json_schema=t.parameters, **({"behavior": beh} if beh else {}))
                 for t in cfg.tools]
        kw["tools"] = [types.Tool(function_declarations=decls)]
    return types.LiveConnectConfig(**kw)


class _Queue:
    """Bounded event queue: when full, the OLDEST audio event is dropped (counted);
    control events are never dropped."""

    def __init__(self, maxsize: int):
        self.maxsize = maxsize
        self._items: list[VoiceEvent] = []
        self._cond = asyncio.Condition()
        self.dropped_audio = 0
        self.closed = False

    async def put(self, ev: VoiceEvent) -> None:
        async with self._cond:
            if len(self._items) >= self.maxsize:
                for i, e in enumerate(self._items):
                    if isinstance(e, ResponseAudio):
                        del self._items[i]
                        self.dropped_audio += 1
                        break
            self._items.append(ev)
            self._cond.notify()

    async def get(self) -> VoiceEvent | None:
        async with self._cond:
            while not self._items and not self.closed:
                await self._cond.wait()
            return self._items.pop(0) if self._items else None

    async def close(self) -> None:
        async with self._cond:
            self.closed = True
            self._cond.notify_all()


class GeminiLiveSession:
    def __init__(self, sdk_session: Any, stack: AsyncExitStack, types: Any, secrets: tuple[str, ...],
                 connect_ms: float, queue_size: int = 512):
        self._s, self._stack, self._types, self._secrets = sdk_session, stack, types, secrets
        self._q = _Queue(queue_size)
        self._tracker = ResponseTracker()
        self._closed = False
        self._reader = asyncio.create_task(self._read_loop(connect_ms))

    @property
    def dropped_audio(self) -> int:
        return self._q.dropped_audio

    async def _read_loop(self, connect_ms: float) -> None:
        await self._q.put(SessionReady(connect_ms))
        try:
            while not self._closed:
                got = False
                async for msg in self._s.receive():       # one model turn per call; loop for the next
                    got = True
                    for ev in map_message(msg, self._tracker):
                        if isinstance(ev, SessionReady):
                            continue
                        await self._q.put(ev)
                if not got:
                    await asyncio.sleep(0.01)             # never spin if receive() yields nothing
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - every transport/API failure becomes one neutral event
            code = getattr(exc, "code", None)
            fatal = code in (400, 401, 403, 404)       # bad key/model/request: retrying will not help
            await self._q.put(Disconnected(safe_error(exc, self._secrets), recoverable=not fatal))
        else:
            await self._q.put(Disconnected("stream ended", recoverable=True))
        finally:
            await self._q.close()

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev

    async def send_audio(self, pcm16_16k: bytes) -> None:
        await self._s.send_realtime_input(audio=self._types.Blob(data=pcm16_16k, mime_type=INPUT_MIME))

    async def end_audio_stream(self) -> None:
        await self._s.send_realtime_input(audio_stream_end=True)

    async def send_text(self, text: str) -> None:
        await self._s.send_realtime_input(text=text)

    async def send_context(self, text: str) -> None:
        """Add context without asking for a reply (turn_complete=False)."""
        t = self._types
        await self._s.send_client_content(turns=t.Content(role="user", parts=[t.Part(text=text)]), turn_complete=False)

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        fr = self._types.FunctionResponse(id=call_id, name=name, response=result)
        await self._s.send_tool_response(function_responses=[fr])

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._reader.cancel()
        try:
            await self._reader
        except asyncio.CancelledError:
            pass
        await self._q.close()
        try:
            await self._stack.aclose()
        except Exception:  # noqa: BLE001, S110 - closing a dead socket may raise; nothing left to release
            pass


class GeminiLiveProvider:
    def __init__(self, api_key: str, model: str, connect_timeout_s: float = 10.0, api_version: str | None = None,
                 sdk_loader: Callable[[], tuple[Any, Any]] | None = None, queue_size: int = 512):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is empty")
        if not model:
            raise ValueError("live model id is empty (set TARA_LIVE_MODEL; ids change, check the models page)")
        self._key, self.model, self.connect_timeout_s = api_key, model, connect_timeout_s
        self.api_version, self._load, self.queue_size = api_version, sdk_loader or _load_sdk, queue_size

    def __repr__(self) -> str:
        return f"GeminiLiveProvider(model={self.model!r})"

    async def open(self, config: VoiceSessionConfig, resume_handle: str | None = None) -> GeminiLiveSession:
        genai, types = self._load()
        version = self.api_version or ("v1beta" if config.proactive_audio else None)   # docs: proactive audio needs v1beta
        http = {"api_version": version} if version else None
        client = genai.Client(api_key=self._key, **({"http_options": http} if http else {}))
        live_cfg = build_live_config(config, resume_handle, types)
        stack = AsyncExitStack()
        t0 = time.monotonic()
        try:
            sess = await asyncio.wait_for(
                stack.enter_async_context(client.aio.live.connect(model=self.model, config=live_cfg)),
                self.connect_timeout_s)
        except TimeoutError as e:
            await stack.aclose()
            raise ProviderTimeout("live connect timed out") from e
        except Exception as e:  # noqa: BLE001 - normalized, redacted
            await stack.aclose()
            cls = FatalProviderError if getattr(e, "code", None) in (400, 401, 403, 404) else ProviderError
            raise cls("live connect failed: " + safe_error(e, (self._key,))) from None
        return GeminiLiveSession(sess, stack, types, (self._key,), (time.monotonic() - t0) * 1000, self.queue_size)


def _load_sdk() -> tuple[Any, Any]:
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise ProviderError("google-genai is not installed (pip install 'google-genai')") from e
    return genai, types
