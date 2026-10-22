"""Audio primitives: bounded ring buffer, resampler, frame segmenter, energy VAD.

All sizes/thresholds are parameters; defaults are placeholders to benchmark
(docs/AUDIO_PIPELINE.md), not tuned values.
"""
from __future__ import annotations

import math
import struct
from array import array
from collections import deque
from dataclasses import dataclass

from .contracts import AudioSegment, PCMChunk, VoiceActivity


class RingBuffer:
    """Bounded byte buffer; overflow drops the oldest bytes and counts them."""

    def __init__(self, capacity_bytes: int):
        if capacity_bytes <= 0:
            raise ValueError("capacity_bytes must be positive")
        self.capacity = capacity_bytes
        self._buf = bytearray()
        self.dropped_bytes = 0

    def write(self, data: bytes) -> None:
        self._buf += data
        extra = len(self._buf) - self.capacity
        if extra > 0:
            del self._buf[:extra]
            self.dropped_bytes += extra

    def read_all(self) -> bytes:
        out = bytes(self._buf)
        self._buf.clear()
        return out

    def __len__(self) -> int:
        return len(self._buf)


def pcm16_samples(data: bytes) -> array:
    if len(data) % 2:
        raise ValueError("PCM16 data must have even length")
    a = array("h")
    a.frombytes(data)
    return a


def resample_pcm16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resampler (adequate for speech VAD/STT input)."""
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("sample rates must be positive")
    if src_rate == dst_rate or not data:
        return data
    s = pcm16_samples(data)
    n_out = max(1, int(len(s) * dst_rate / src_rate))
    out = array("h")
    ratio = (len(s) - 1) / max(1, n_out - 1) if n_out > 1 else 0.0
    for i in range(n_out):
        pos = i * ratio
        j = int(pos)
        frac = pos - j
        nxt = s[min(j + 1, len(s) - 1)]
        out.append(int(s[j] + (nxt - s[j]) * frac))
    return out.tobytes()


def rms(data: bytes) -> float:
    s = pcm16_samples(data)
    if not s:
        return 0.0
    return math.sqrt(sum(x * x for x in s) / len(s))


def tone(ms: int, rate: int = 16000, amp: int = 8000, freq: int = 220) -> bytes:
    """Synthetic voiced-like test signal."""
    n = rate * ms // 1000
    return struct.pack(f"<{n}h", *(int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))


def silence(ms: int, rate: int = 16000) -> bytes:
    return b"\x00\x00" * (rate * ms // 1000)


class EnergyVad:
    """Simple RMS gate. A fallback and test double; real deployments use Silero."""

    def __init__(self, threshold: float = 500.0, frame_ms: int = 30, sample_rate: int = 16000):
        self.threshold, self.frame_ms, self.sample_rate = threshold, frame_ms, sample_rate

    def is_speech(self, frame: bytes) -> bool:
        return rms(frame) >= self.threshold


class AdaptiveEnergyVad:
    """RMS gate that follows the background level, so steady hiss, hum or a distant fan is not mistaken for someone talking.

    The noise floor falls quickly when it gets quieter and rises only slowly, so a sentence barely moves it but constant noise is learned
    within about fifteen seconds. Speech must exceed `ratio` times the floor and also the fixed `base_threshold`.
    """

    def __init__(self, base_threshold: float = 500.0, ratio: float = 3.0, rise: float = 0.002, frame_ms: int = 30, sample_rate: int = 16000):
        self.base_threshold, self.ratio, self.rise = base_threshold, ratio, rise
        self.frame_ms, self.sample_rate = frame_ms, sample_rate
        self.floor: float | None = None

    @property
    def threshold(self) -> float:
        return max(self.base_threshold, (self.floor or 0.0) * self.ratio)

    def is_speech(self, frame: bytes) -> bool:
        r = rms(frame)
        if self.floor is None:
            self.floor = r
        elif r < self.floor:
            self.floor = 0.8 * self.floor + 0.2 * r
        else:
            self.floor += (r - self.floor) * self.rise
        return r >= self.threshold


class AdaptiveGain:
    """Brings quiet voices up to a steady level before the speech model hears them, and never boosts silence or steady noise.

    Per chunk: measure the level, follow the noise floor (as the adaptive VAD does), and only chunks clearly above the floor and above
    `min_level` count as speech. Speech gets a gain that moves toward `target / level` (capped at `max_gain`): it comes down fast when a
    voice gets loud and rises slowly, so a quiet voice is lifted without pumping. Nothing is added to non-speech chunks (the gain holds).
    Samples are hard-limited to 16 bit. Cost is one pass over the chunk, no extra delay.
    """

    def __init__(self, target: float = 2500.0, max_gain: float = 8.0, min_level: float = 60.0, floor_ratio: float = 2.0,
                 rise: float = 0.002, attack: float = 0.6, release: float = 0.15):
        self.target, self.max_gain, self.min_level, self.floor_ratio = target, max_gain, min_level, floor_ratio
        self.rise, self.attack, self.release = rise, attack, release
        self.gain = 1.0
        self.floor: float | None = None

    def process(self, data: bytes) -> bytes:
        s = pcm16_samples(data)
        if not s:
            return data
        level = math.sqrt(sum(x * x for x in s) / len(s))
        if self.floor is None:
            self.floor = level
        elif level < self.floor:
            self.floor = 0.8 * self.floor + 0.2 * level
        else:
            self.floor += (level - self.floor) * self.rise
        if level >= max(self.min_level, self.floor * self.floor_ratio):
            want = min(self.max_gain, max(1.0, self.target / level))
            k = self.attack if want < self.gain else self.release
            self.gain += (want - self.gain) * k
        if self.gain < 1.05:
            return data
        g = self.gain
        out = array("h", (max(-32768, min(32767, int(x * g))) for x in s))
        return out.tobytes()


@dataclass(frozen=True)
class SpeechStarted:
    stream_id: str
    ts_ms: int


@dataclass(frozen=True)
class SegmentReady:
    segment: AudioSegment
    forced_split: bool = False


class Segmenter:
    """Turns frames + a VAD into speech segments.

    onset: `min_speech_ms` of consecutive speech before declaring start (rejects clicks).
    endpoint: `hangover_ms` of silence closes the segment (NOT the end of the *turn*).
    pre-roll keeps audio before onset so first phonemes are not clipped.
    `max_segment_ms` bounds memory and forces a split in unbroken speech.
    """

    def __init__(self, vad: VoiceActivity, stream_id: str = "mix", min_speech_ms: int = 90,
                 hangover_ms: int = 300, preroll_ms: int = 150, max_segment_ms: int = 15000):
        self.vad, self.stream_id = vad, stream_id
        self.min_speech_ms, self.hangover_ms = min_speech_ms, hangover_ms
        self.preroll_ms, self.max_segment_ms = preroll_ms, max_segment_ms
        self._frame_bytes = vad.sample_rate * vad.frame_ms // 1000 * 2
        self._carry = b""
        self._preroll: deque[bytes] = deque(maxlen=max(1, preroll_ms // vad.frame_ms))
        self._active: list[bytes] = []
        self._pending_onset: list[bytes] = []
        self._silence_ms = 0
        self._speech_run_ms = 0
        self._in_speech = False
        self._seg_start_ms = 0
        self._clock_ms = 0  # time of next frame

    def feed(self, chunk: PCMChunk) -> list[SpeechStarted | SegmentReady]:
        if chunk.sample_rate != self.vad.sample_rate:
            raise ValueError(f"expected {self.vad.sample_rate} Hz, got {chunk.sample_rate}")
        if not self._carry and not self._in_speech and not self._pending_onset:
            self._clock_ms = chunk.ts_ms
        data = self._carry + chunk.data
        out: list[SpeechStarted | SegmentReady] = []
        n = len(data) // self._frame_bytes
        for i in range(n):
            frame = data[i * self._frame_bytes:(i + 1) * self._frame_bytes]
            out += self._frame(frame)
            self._clock_ms += self.vad.frame_ms
        self._carry = data[n * self._frame_bytes:]
        return out

    def flush(self) -> list[SpeechStarted | SegmentReady]:
        if self._in_speech and self._active:
            return [self._close(forced=False)]
        return []

    def _frame(self, frame: bytes) -> list[SpeechStarted | SegmentReady]:
        fms = self.vad.frame_ms
        speech = self.vad.is_speech(frame)
        out: list[SpeechStarted | SegmentReady] = []
        if not self._in_speech:
            if speech:
                self._pending_onset.append(frame)
                self._speech_run_ms += fms
                if self._speech_run_ms >= self.min_speech_ms:
                    pre = list(self._preroll)
                    self._active = pre + self._pending_onset
                    self._seg_start_ms = self._clock_ms + fms - (len(self._active) * fms)
                    self._in_speech, self._silence_ms = True, 0
                    self._pending_onset, self._speech_run_ms = [], 0
                    out.append(SpeechStarted(self.stream_id, self._seg_start_ms + len(pre) * fms))
            else:
                self._pending_onset, self._speech_run_ms = [], 0
                self._preroll.append(frame)
            return out
        self._active.append(frame)
        self._silence_ms = 0 if speech else self._silence_ms + fms
        if self._silence_ms >= self.hangover_ms:
            out.append(self._close(forced=False))
        elif len(self._active) * fms >= self.max_segment_ms:
            out.append(self._close(forced=True))
        return out

    def _close(self, forced: bool) -> SegmentReady:
        fms = self.vad.frame_ms
        pcm = b"".join(self._active)
        end = self._seg_start_ms + len(self._active) * fms
        seg = AudioSegment(self.stream_id, pcm, self.vad.sample_rate, self._seg_start_ms, end)
        self._active, self._in_speech, self._silence_ms = [], False, 0
        self._preroll.clear()
        return SegmentReady(seg, forced_split=forced)
