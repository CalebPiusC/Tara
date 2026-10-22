import pytest

from tara.core.contracts import PCMChunk
from tara.core.events import Event, EventBus
from tara.floor.audio import EnergyVad, RingBuffer, Segmenter, SegmentReady, SpeechStarted, resample_pcm16, rms, silence, tone
from tara.floor.turn import TurnParams, looks_complete, required_silence_ms, turn_ended


def feed(seg, pcm, t0=0):
    out = []
    step = 320 * 2  # 20 ms
    for i in range(0, len(pcm), step):
        out += seg.feed(PCMChunk(pcm[i:i + step], 16000, t0 + i // 32))
    return out


def mk(**kw):
    return Segmenter(EnergyVad(), **kw)


def test_ring_buffer_bounded_and_counts_drops():
    r = RingBuffer(10)
    r.write(b"x" * 25)
    assert len(r) == 10 and r.dropped_bytes == 15
    assert r.read_all() == b"x" * 10 and len(r) == 0
    with pytest.raises(ValueError):
        RingBuffer(0)


def test_resample_length_and_identity():
    d = tone(100, 16000)
    assert resample_pcm16(d, 16000, 16000) is d
    assert abs(len(resample_pcm16(d, 16000, 8000)) - len(d) // 2) <= 4
    assert abs(len(resample_pcm16(d, 16000, 24000)) - len(d) * 3 // 2) <= 6
    with pytest.raises(ValueError):
        resample_pcm16(d, 0, 8000)
    with pytest.raises(ValueError):
        resample_pcm16(b"\x00", 16000, 8000)


def test_tone_has_energy_and_silence_none():
    assert rms(tone(30)) > 1000 and rms(silence(30)) == 0


def test_segmenter_emits_one_segment_with_preroll():
    s = mk(hangover_ms=300, preroll_ms=150)
    out = feed(s, silence(500) + tone(600) + silence(600))
    starts = [o for o in out if isinstance(o, SpeechStarted)]
    segs = [o.segment for o in out if isinstance(o, SegmentReady)]
    assert len(starts) == 1 and len(segs) == 1
    seg = segs[0]
    assert seg.start_ms <= 500 - 100          # pre-roll included before onset at 500 ms
    assert seg.end_ms - seg.start_ms >= 600


def test_short_click_is_rejected():
    s = mk(min_speech_ms=90)
    out = feed(s, silence(300) + tone(30) + silence(800))
    assert out == []


def test_short_pause_inside_speech_does_not_split():
    s = mk(hangover_ms=300)
    out = feed(s, tone(400) + silence(150) + tone(400) + silence(600))
    assert len([o for o in out if isinstance(o, SegmentReady)]) == 1


def test_long_pause_splits():
    s = mk(hangover_ms=300)
    out = feed(s, tone(400) + silence(500) + tone(400) + silence(500))
    assert len([o for o in out if isinstance(o, SegmentReady)]) == 2


def test_max_segment_forces_split_and_bounds_memory():
    s = mk(max_segment_ms=1000)
    out = feed(s, tone(3500) + silence(500))
    segs = [o for o in out if isinstance(o, SegmentReady)]
    assert len(segs) >= 3 and segs[0].forced_split
    assert all(o.segment.end_ms - o.segment.start_ms <= 1100 for o in segs)


def test_flush_closes_open_segment_and_wrong_rate_rejected():
    s = mk()
    feed(s, tone(500))
    assert len([o for o in s.flush() if isinstance(o, SegmentReady)]) == 1
    with pytest.raises(ValueError):
        s.feed(PCMChunk(b"\x00\x00" * 10, 8000, 0))


# ---- turn ---------------------------------------------------------------
def test_completeness_heuristics():
    assert looks_complete("I would use an idempotency key.")
    assert not looks_complete("I would use a unique constraint and")
    assert not looks_complete("so, um...")
    assert not looks_complete("")


def test_incomplete_text_needs_longer_silence():
    p = TurnParams()
    assert required_silence_ms("so the thing is, um", p) > required_silence_ms("That is my answer.", p)
    assert not turn_ended(900, "so the thing is, um", p)
    assert turn_ended(900, "That is my answer.", p)


def test_interview_patience_and_open_question():
    p = TurnParams(patience_multiplier=2.0)
    base = required_silence_ms("done.", TurnParams())
    assert required_silence_ms("done.", p) == base * 2
    assert required_silence_ms("done.", p, after_open_question=True) > required_silence_ms("done.", p)


def test_max_wait_cap_and_other_started():
    p = TurnParams(max_wait_ms=1000, patience_multiplier=10)
    assert required_silence_ms("um", p) == 1000
    assert turn_ended(0, "um", p, other_started=True)


# ---- events ---------------------------------------------------------------
def test_bus_prefix_and_bounded_drop():
    bus = EventBus()
    a, b = bus.subscribe("vad."), bus.subscribe("", maxsize=2)
    for n in ("vad.speech_started", "x.y", "vad.speech_ended"):
        bus.publish(Event(n, 0, 0))
    assert a.queue.qsize() == 2 and b.queue.qsize() == 2 and b.dropped == 1
    assert bus.names() == ["vad.speech_started", "x.y", "vad.speech_ended"]


def test_bus_history_bounded():
    bus = EventBus()
    bus.history_limit = 5
    for i in range(20):
        bus.publish(Event(f"e{i}", 0, 0))
    assert len(bus.history) == 5 and bus.history[-1].name == "e19"
