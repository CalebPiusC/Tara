"""Local microphone/speaker adapter for a single-person voice demo.

This is not a meeting-platform adapter and it does not touch Google Meet. It reads the
default input device at 16 kHz mono PCM16 and plays Tara's 24 kHz mono PCM16 through the
default output device, resampling to that device's default rate. Use headphones: otherwise
Tara's own reply can be picked up by the microphone and sent back to the model.

The adapter is deliberately small. It reuses the same `MeetingAdapter` contract as the
scripted local adapter, so the engine, gate, controls and playback cancellation are
unchanged.
"""
from __future__ import annotations

import array
import asyncio
import itertools
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..audio import resample_pcm16
from ..contracts import AdapterCapabilities, DeliveryReceipt, MeetingEvent, MeetingEventKind, MeetingTarget, PCMChunk
from ..errors import JoinError

MIC_RATE = 16000
OUT_RATE = 24000
MIC_BLOCK = 480          # 30 ms at 16 kHz, matching the engine's segmenter frame
MAX_STOPPED_IDS = 64


class AudioBackend(Protocol):
    """Minimal sounddevice surface, injectable for offline tests."""

    def open_input(self, *, callback: Any, samplerate: int, channels: int, dtype: str, blocksize: int) -> Any: ...

    def open_output(self, *, samplerate: int, channels: int, dtype: str) -> Any: ...

    def default_output_rate(self) -> int: ...


class SoundDeviceBackend:
    """Lazy sounddevice wrapper. The SDK is an optional audio extra, not a model."""

    def __init__(self, input_device: str | int | None = None, output_device: str | int | None = None, sd_module: Any = None,
                 output_latency: str | float | None = None) -> None:
        """`input_device` / `output_device` pick a specific device by name (a part of the name is enough) or index; None means the default."""
        if sd_module is None:
            try:
                import sounddevice
            except ImportError as exc:  # pragma: no cover - exercised only without the extra
                raise JoinError("sounddevice is not installed; install with `pip install -e '.[audio]'`") from exc
            sd_module = sounddevice
        self._sd = sd_module
        self._latency = output_latency          # "high" gives the output a bigger cushion against gaps (a virtual cable has no speaker to protect)
        self._in = self._resolve(input_device, "input") if input_device is not None else None
        self._out = self._resolve(output_device, "output") if output_device is not None else None

    def _resolve(self, device: str | int, kind: str) -> int:
        if isinstance(device, int):
            return device
        key = "max_input_channels" if kind == "input" else "max_output_channels"
        for i, d in enumerate(self._sd.query_devices()):
            if device.lower() in str(d["name"]).lower() and int(d[key]) > 0:
                return i
        raise JoinError(f"no {kind} audio device matching {device!r}")

    def open_input(self, *, callback: Any, samplerate: int, channels: int, dtype: str, blocksize: int) -> Any:
        kw: dict[str, Any] = {} if self._in is None else {"device": self._in}
        return self._sd.RawInputStream(samplerate=samplerate, channels=channels, dtype=dtype, blocksize=blocksize,
                                       callback=callback, **kw)

    def open_output(self, *, samplerate: int, channels: int, dtype: str) -> Any:
        kw: dict[str, Any] = {} if self._out is None else {"device": self._out}
        if self._latency is not None:
            kw["latency"] = self._latency
        return self._sd.RawOutputStream(samplerate=samplerate, channels=channels, dtype=dtype, **kw)

    def default_output_rate(self) -> int:
        try:
            q = self._sd.query_devices(self._out) if self._out is not None else self._sd.query_devices(kind="output")
            return int(q["default_samplerate"])
        except Exception:  # noqa: BLE001 - fall back to the provider's rate
            return OUT_RATE


def expand_channels(pcm: bytes, channels: int) -> bytes:
    """Copy mono PCM16 into every channel of an interleaved stream."""
    if channels <= 1 or not pcm:
        return pcm
    mono = array.array("h")
    mono.frombytes(pcm)
    out = array.array("h", [0]) * (len(mono) * channels)
    for c in range(channels):
        out[c::channels] = mono
    return out.tobytes()


@dataclass
class _Playback:
    uid: str
    stream: Any | None = None
    delivered_ms: int = 0
    stopped: bool = False
    write_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    close_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


def _write(state: _Playback, data: bytes) -> bool:
    """Write one chunk; never run close() concurrently with this write."""
    with state.write_lock:
        stream = state.stream
        if stream is None:
            return False
        stream.write(data)
        return True


def _abort_stream(state: _Playback) -> None:
    """Stop queued audio without closing the stream; send_audio still owns close()."""
    with state.close_lock:
        stream = state.stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:  # noqa: BLE001 - a broken sink must not block cancellation
                pass


def _close_stream(state: _Playback, *, drain: bool) -> None:
    """Abort if needed, wait for an in-flight write, then close exactly once."""
    with state.close_lock:
        stream, state.stream = state.stream, None
        if stream is None:
            return
        try:
            if drain:
                stream.stop()       # let already-written audio play out
            else:
                stream.abort()      # discard queued audio for an interruption or failure
        except Exception:  # noqa: BLE001 - a broken sink must not block cancellation
            pass
        with state.write_lock:      # a cancelled write may still be unwinding in its worker thread
            try:
                stream.close()
            except Exception:  # noqa: BLE001 - best-effort close
                pass


class AudioDeviceAdapter:
    """One microphone stream and one output stream, no platform features."""

    def __init__(self, backend: AudioBackend | None = None, *, stream_id: str = "you", mic_rate: int = MIC_RATE,
                 out_rate: int = OUT_RATE, blocksize: int = MIC_BLOCK, queue_chunks: int = 100,
                 half_duplex: bool = False, out_channels: int = 1, prebuffer_ms: int = 0):
        self.capabilities = AdapterCapabilities(per_participant_audio=True, delivery_confirmation=True,
                                                can_mute_self=False, can_camera=False, can_show_image=False,
                                                can_mute_others=False)
        self.stream_id = stream_id
        self.mic_rate, self.out_rate, self.blocksize = mic_rate, out_rate, blocksize
        self.prebuffer_ms = max(0, prebuffer_ms)     # collect this much of a reply before the first write, so late chunks do not leave gaps
        self.out_channels = max(1, out_channels)     # a virtual cable often wants stereo: the mono voice is copied to every channel
        self.half_duplex = half_duplex          # speaker mode: mic sends silence while Tara speaks (no echo)
        self._backend = backend
        self._queue: asyncio.Queue[MeetingEvent | None] = asyncio.Queue(maxsize=queue_chunks)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._input: Any | None = None
        self._play: _Playback | None = None
        self._stopped: deque[str] = deque(maxlen=MAX_STOPPED_IDS)
        self._seq = itertools.count(1)
        self._t0 = 0.0
        self._joined = False
        self._closed = False
        self.dropped_chunks = 0
        self.input_chunks = 0
        self.input_bytes = 0
        self.playback_failures = 0

    # ---- lifecycle -------------------------------------------------------------------------
    async def join(self, target: MeetingTarget | None = None) -> None:
        if self._joined:
            return
        self._closed = False
        while not self._queue.empty():          # a reconnect must not inherit a previous shutdown sentinel
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                break
        self._loop = asyncio.get_running_loop()
        self._t0 = time.monotonic()
        backend = self._backend or SoundDeviceBackend()
        self._backend = backend
        try:
            rate = int(backend.default_output_rate())
        except Exception:  # noqa: BLE001 - use the documented fallback when the query fails
            rate = self.out_rate
        if rate > 0:
            self.out_rate = rate
        try:
            self._input = backend.open_input(callback=self._on_mic, samplerate=self.mic_rate, channels=1,
                                             dtype="int16", blocksize=self.blocksize)
            self._input.start()
        except Exception as exc:
            self._close_input()
            raise JoinError(f"could not open the microphone: {exc}") from exc
        # Probe the output now so a missing/busy speaker device fails at start, not on the first reply.
        probe: Any | None = None
        try:
            probe = backend.open_output(samplerate=self.out_rate, channels=self.out_channels, dtype="int16")
            probe.start()
            probe.stop()
        except Exception as exc:
            self._close_input()
            raise JoinError(f"could not open the speakers at {self.out_rate} Hz: {exc}") from exc
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception:  # noqa: BLE001 - best-effort close
                    pass
        self._joined = True

    async def leave(self) -> None:
        self._closed = True
        self._close_input()
        state = self._play
        if state is not None and state.stream is not None:
            state.stopped = True
            try:
                await asyncio.to_thread(_close_stream, state, drain=False)
            except Exception:  # noqa: BLE001 - leaving must not raise
                pass
        self._joined = False
        while not self._queue.empty():          # drop stale mic frames; shutdown must not wait on them
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                break
        self._queue.put_nowait(None)

    async def events(self) -> AsyncIterator[MeetingEvent]:
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    # ---- microphone -------------------------------------------------------------------------
    def _on_mic(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if self._closed or self._loop is None:
            return
        data = bytes(indata)
        if not data:
            return
        if self.half_duplex and self._play is not None:
            data = b"\x00" * len(data)      # do not send the speaker's own reply back to the model
        self.input_chunks += 1
        self.input_bytes += len(data)
        ts = int((time.monotonic() - self._t0) * 1000)
        ev = MeetingEvent(MeetingEventKind.AUDIO, ts, f"mic-{next(self._seq)}",
                          {"chunk": PCMChunk(data, self.mic_rate, ts, self.stream_id)})
        try:
            self._loop.call_soon_threadsafe(self._push, ev)
        except RuntimeError:  # loop already closed
            pass

    def _push(self, ev: MeetingEvent) -> None:
        if self._closed:
            return
        if self._queue.full():
            try:
                self._queue.get_nowait()          # drop the oldest frame; lagging audio is worse than a gap
                self.dropped_chunks += 1
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
        self._queue.put_nowait(ev)

    def _close_input(self) -> None:
        stream, self._input = self._input, None
        if stream is not None:
            try:
                stream.stop()
            except Exception:  # noqa: BLE001 - best-effort close
                pass
            try:
                stream.close()
            except Exception:  # noqa: BLE001 - best-effort close
                pass

    # ---- speaker ----------------------------------------------------------------------------
    async def send_audio(self, utterance_id: str, chunks: AsyncIterator[PCMChunk]) -> DeliveryReceipt:
        if self._closed or not self._joined or self._backend is None:
            return DeliveryReceipt(utterance_id, 0, False)
        state = _Playback(utterance_id)
        self._play = state
        if utterance_id in self._stopped:      # stop_audio may have won the race before state existed
            self._play = None
            return DeliveryReceipt(utterance_id, 0, False)
        stream = None
        try:
            stream = self._backend.open_output(samplerate=self.out_rate, channels=self.out_channels, dtype="int16")
            stream.start()
            state.stream = stream
        except Exception:  # noqa: BLE001 - playback failure is reported, never assumed
            self.playback_failures += 1
            if state.stream is not None:
                await asyncio.to_thread(_close_stream, state, drain=False)
            elif stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001 - best-effort close
                    pass
            self._play = None
            return DeliveryReceipt(utterance_id, 0, False)
        completed = False
        pre: list[bytes] = []
        pre_ms = 0
        started = self.prebuffer_ms <= 0
        try:
            async for chunk in chunks:
                if state.stopped or self._closed:
                    break
                data = chunk.data
                if not data:
                    continue
                if chunk.sample_rate != self.out_rate:
                    data = resample_pcm16(data, chunk.sample_rate, self.out_rate)
                if self.out_channels > 1:
                    data = expand_channels(data, self.out_channels)
                if not started:
                    pre.append(data)
                    pre_ms += int(len(data) / 2 / (self.out_rate * self.out_channels) * 1000)
                    if pre_ms < self.prebuffer_ms:
                        continue
                    started, data, pre = True, b"".join(pre), []
                try:
                    written = await asyncio.to_thread(_write, state, data)
                except Exception:  # noqa: BLE001 - a broken sink ends this response only
                    if not state.stopped:
                        self.playback_failures += 1
                    break
                if not written:
                    break
                if not state.stopped:
                    state.delivered_ms += int(len(data) / 2 / (self.out_rate * self.out_channels) * 1000)
            else:
                if pre and not state.stopped and not self._closed:         # a short reply: write what was held back
                    data = b"".join(pre)
                    try:
                        if await asyncio.to_thread(_write, state, data):
                            state.delivered_ms += int(len(data) / 2 / (self.out_rate * self.out_channels) * 1000)
                    except Exception:  # noqa: BLE001
                        self.playback_failures += 1
                completed = not state.stopped and not self._closed
        finally:
            await asyncio.to_thread(_close_stream, state, drain=completed)
            if self._play is state:
                self._play = None
        return DeliveryReceipt(utterance_id, state.delivered_ms, completed)

    async def stop_audio(self, utterance_id: str) -> int:
        self._stopped.append(utterance_id)
        state = self._play
        if state is None or state.uid != utterance_id:
            return 0
        state.stopped = True
        await asyncio.to_thread(_abort_stream, state)   # send_audio waits for the write, then closes
        return state.delivered_ms
