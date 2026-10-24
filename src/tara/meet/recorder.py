"""Opt-in recording of a meeting's audio to local WAV files. Independent of Gemini: it only copies the PCM the adapter already handles.

Two tracks, aligned to the same wall clock so they can be mixed later: `room` (what the meeting sounded like to Tara) and `tara`
(her own voice). Files are plain 16-bit mono WAV under the work folder (git-ignored). Nothing is sent anywhere; a failure here
disables the recorder and never touches the meeting.

With per-participant capture the room audio arrives as several channels at once, and writing them into one track would interleave
them into a timeline nobody spoke. Each channel therefore gets its own file (`...-room-ch1.wav`), on the same clock as the others.
"""
from __future__ import annotations

import struct
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

MAX_CHANNEL_FILES = 64            # the bridge never reports more channels than this; a file per channel is bounded on purpose


class _Track:
    def __init__(self, path: Path, clock: Callable[[], float], t0: float):
        self.path, self.clock, self.t0 = path, clock, t0
        self.rate = 0
        self.fh: BinaryIO | None = None
        self.frames = 0
        self._last_patch = t0

    def _open(self, rate: int) -> None:
        self.rate = rate
        self.fh = self.path.open("wb")
        self.fh.write(self._header(0))

    def _header(self, data_bytes: int) -> bytes:
        return (b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, self.rate, self.rate * 2, 2, 16)
                + b"data" + struct.pack("<I", data_bytes))

    def write(self, pcm: bytes, rate: int) -> None:
        if not pcm:
            return
        if self.fh is None:
            self._open(rate)
        assert self.fh is not None
        now = self.clock()
        behind = int((now - self.t0) * self.rate) - self.frames - len(pcm) // 2
        if behind > self.rate // 5:                                   # a gap longer than 0.2 s: keep the tracks on the same timeline
            self.fh.write(b"\x00\x00" * behind)
            self.frames += behind
        self.fh.write(pcm)
        self.frames += len(pcm) // 2
        if now - self._last_patch > 5:                                # keep the header valid so a crash still leaves a playable file
            self.flush()
            self._last_patch = now

    def flush(self) -> None:
        if self.fh is None:
            return
        self.fh.flush()
        pos = self.fh.tell()
        self.fh.seek(0)
        self.fh.write(self._header(self.frames * 2))
        self.fh.seek(pos)
        self.fh.flush()

    def close(self) -> None:
        if self.fh is not None:
            self.flush()
            self.fh.close()
            self.fh = None


class MeetingRecorder:
    def __init__(self, folder: Path, name: str, clock: Callable[[], float] = time.monotonic):
        folder.mkdir(parents=True, exist_ok=True)
        t0 = clock()
        self.room = _Track(folder / f"{name}-room.wav", clock, t0)
        self.tara = _Track(folder / f"{name}-tara.wav", clock, t0)
        self.channels: dict[int, _Track] = {}          # per-participant room audio: one file per channel, same clock
        self.failed = ""

    @property
    def paths(self) -> tuple[Path, Path]:
        return self.room.path, self.tara.path

    def write_room(self, pcm: bytes, rate: int, channel: int | None = None) -> None:
        """Room audio. `channel` is set only by per-participant capture, and each channel is written to its own file."""
        if channel is None:
            self._safe(self.room, pcm, rate)
            return
        track = self.channels.get(channel)
        if track is None:
            if len(self.channels) >= MAX_CHANNEL_FILES:
                self.failed = f"too many channels to record (limit {MAX_CHANNEL_FILES})"
                return
            base = self.room.path
            track = self.channels[channel] = _Track(base.with_name(f"{base.stem}-ch{channel}{base.suffix}"), self.room.clock,
                                                    self.room.t0)
        self._safe(track, pcm, rate)

    def write_tara(self, pcm: bytes, rate: int) -> None:
        self._safe(self.tara, pcm, rate)

    def _safe(self, track: _Track, pcm: bytes, rate: int) -> None:
        if self.failed:
            return
        try:
            track.write(pcm, rate)
        except Exception as exc:  # noqa: BLE001 - recording must never break the meeting
            self.failed = f"{type(exc).__name__}: {exc}"

    def close(self) -> None:
        for t in (self.room, self.tara, *self.channels.values()):
            try:
                t.close()
            except Exception:  # noqa: BLE001
                pass
