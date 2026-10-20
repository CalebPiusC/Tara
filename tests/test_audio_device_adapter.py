"""Offline tests for the local microphone/speaker adapter, using a fake audio backend."""
import asyncio
import threading
from collections.abc import AsyncIterator

import pytest

from tara.adapters.audio_device import AudioDeviceAdapter
from tara.contracts import MeetingEventKind, PCMChunk
from tara.errors import JoinError


class FakeInput:
    def __init__(self, **kw):
        self.kw, self.callback = kw, kw["callback"]
        self.started = self.stopped = self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def feed(self, data: bytes):
        self.callback(data, len(data) // 2, None, None)


class FakeOutput:
    def __init__(self, **kw):
        self.kw = kw
        self.writes: list[bytes] = []
        self.started = self.stopped = self.aborted = self.closed = False
        self.entered = threading.Event()
        self.block = threading.Event()

    def start(self):
        self.started = True

    def write(self, data: bytes):
        self.entered.set()
        self.block.wait(2)
        self.writes.append(data)

    def stop(self):
        self.stopped = True

    def abort(self):
        self.aborted = True
        self.block.set()

    def close(self):
        self.closed = True


class FakeBackend:
    def __init__(self, fail_output: bool = False, output_rate: int = 24000):
        self.inputs: list[FakeInput] = []
        self.outputs: list[FakeOutput] = []
        self.fail_output = fail_output
        self.output_rate = output_rate

    def default_output_rate(self) -> int:
        return self.output_rate

    def open_input(self, **kw):
        stream = FakeInput(**kw)
        self.inputs.append(stream)
        return stream

    def open_output(self, **kw):
        if self.fail_output:
            raise RuntimeError("no output device")
        stream = FakeOutput(**kw)
        self.outputs.append(stream)
        return stream


async def one_chunk(data: bytes, sample_rate: int = 16000) -> AsyncIterator[PCMChunk]:
    yield PCMChunk(data, sample_rate, 0)


def run(coro):
    return asyncio.run(coro)


def test_join_emits_mic_audio_and_leave_ends_the_stream():
    async def go():
        backend = FakeBackend()
        adapter = AudioDeviceAdapter(backend, stream_id="you")
        await adapter.join(None)
        assert backend.inputs[0].started
        assert backend.outputs[0].started and backend.outputs[0].stopped and backend.outputs[0].closed

        backend.inputs[0].feed(b"\x01\x00" * 480)
        events = adapter.events()
        ev = await asyncio.wait_for(anext(events), 1)
        assert ev.kind is MeetingEventKind.AUDIO
        assert ev.payload["chunk"].data == b"\x01\x00" * 480
        assert ev.payload["chunk"].sample_rate == 16000
        assert ev.payload["chunk"].stream_id == "you"

        await adapter.leave()
        assert backend.inputs[0].stopped and backend.inputs[0].closed
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(events), 1)

    run(go())


def test_send_audio_resamples_to_24k_and_reports_delivery():
    async def go():
        backend = FakeBackend()
        adapter = AudioDeviceAdapter(backend, stream_id="you")
        await adapter.join(None)
        receipt = await adapter.send_audio("live_1", one_chunk(b"\x00\x00" * 320))
        out = backend.outputs[-1]
        assert receipt.completed and receipt.delivered_ms > 0
        assert len(out.writes) == 1 and len(out.writes[0]) == 960          # 320 samples at 16k -> 480 at 24k
        assert out.stopped and out.closed

    run(go())


def test_output_uses_the_device_default_rate_and_resamples():
    async def go():
        backend = FakeBackend(output_rate=48000)
        adapter = AudioDeviceAdapter(backend, stream_id="you")
        await adapter.join(None)
        assert adapter.out_rate == 48000
        receipt = await adapter.send_audio("live_1", one_chunk(b"\x00\x00" * 320))
        out = backend.outputs[-1]
        assert receipt.completed and out.kw["samplerate"] == 48000
        assert len(out.writes) == 1 and len(out.writes[0]) == 1920          # 20 ms at 48 kHz

    run(go())


def test_leave_ends_events_even_when_the_queue_is_full():
    async def go():
        backend = FakeBackend()
        adapter = AudioDeviceAdapter(backend, stream_id="you", queue_chunks=1)
        await adapter.join(None)
        for _ in range(3):
            backend.inputs[0].feed(b"\x00\x00" * 480)
        await asyncio.sleep(0)
        await adapter.leave()
        events = adapter.events()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(events), 1)

    run(go())


def test_half_duplex_sends_silence_while_tara_speaks():
    async def go():
        backend = FakeBackend()
        adapter = AudioDeviceAdapter(backend, stream_id="you", half_duplex=True)
        await adapter.join(None)
        events = adapter.events()
        task = asyncio.create_task(adapter.send_audio("live_1", one_chunk(b"\x00\x00" * 320)))
        for _ in range(200):
            if len(backend.outputs) > 1 and backend.outputs[-1].entered.is_set():
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("playback did not start")
        backend.inputs[0].feed(b"\x01\x00" * 480)
        ev = await asyncio.wait_for(anext(events), 1)
        assert ev.payload["chunk"].data == b"\x00\x00" * 480   # echo is not sent back to the model
        await adapter.stop_audio("live_1")
        await asyncio.wait_for(task, 2)

    run(go())


def test_stop_before_playback_starts_is_honored():
    async def go():
        backend = FakeBackend()
        adapter = AudioDeviceAdapter(backend, stream_id="you")
        await adapter.join(None)
        assert await adapter.stop_audio("live_1") == 0
        receipt = await adapter.send_audio("live_1", one_chunk(b"\x00\x00" * 160))
        assert receipt.completed is False and receipt.delivered_ms == 0
        assert backend.outputs[-1].writes == []

    run(go())


def test_stop_during_playback_aborts_the_sink_and_reports_partial_delivery():
    async def go():
        backend = FakeBackend()
        adapter = AudioDeviceAdapter(backend, stream_id="you")
        await adapter.join(None)
        task = asyncio.create_task(adapter.send_audio("live_1", one_chunk(b"\x00\x00" * 320)))
        # The output stream is opened lazily inside send_audio, so wait for it to appear and block.
        for _ in range(200):
            if len(backend.outputs) > 1 and backend.outputs[-1].entered.is_set():
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("playback did not start")
        out = backend.outputs[-1]
        delivered = await adapter.stop_audio("live_1")
        receipt = await asyncio.wait_for(task, 2)
        assert delivered == receipt.delivered_ms
        assert receipt.completed is False and out.aborted and out.closed

    run(go())


def test_cancelled_playback_still_closes_the_sink_without_hanging():
    async def go():
        backend = FakeBackend()
        adapter = AudioDeviceAdapter(backend, stream_id="you")
        await adapter.join(None)
        task = asyncio.create_task(adapter.send_audio("live_1", one_chunk(b"\x00\x00" * 320)))
        for _ in range(200):
            if len(backend.outputs) > 1 and backend.outputs[-1].entered.is_set():
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("playback did not start")
        out = backend.outputs[-1]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert out.aborted and out.closed

    run(go())


def test_output_device_failure_is_a_join_error_and_closes_the_mic():
    async def go():
        backend = FakeBackend(fail_output=True)
        adapter = AudioDeviceAdapter(backend, stream_id="you")
        with pytest.raises(JoinError, match="speakers"):
            await adapter.join(None)
        assert backend.inputs[0].stopped and backend.inputs[0].closed

    run(go())
