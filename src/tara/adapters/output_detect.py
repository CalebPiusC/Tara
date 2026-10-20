"""Best-effort detection of whether the active audio output is private (headphones) or open (speakers).

Why it matters: open speakers let the microphone hear Tara and feed her own voice back to the model, which
makes her interrupt herself. Headphones do not. The safe fallback for anything uncertain is "speaker"
(half-duplex: the mic is muted while Tara speaks), which costs voice barge-in but can never cause feedback.

What can and cannot be known (macOS, verified on one machine with built-in speakers only):
- `sounddevice` gives only the output device NAME. Wired headphones in the jack keep the same device name.
- `system_profiler SPAudioDataType` reports `Transport` and an `Output Source` for the default output. The built-in
  device showed `Output Source: MacBook Pro Speakers`. The string macOS shows for wired headphones was NOT
  observed here (none plugged in), so a "Headphones" match is an assumption until tested with real wired headphones.
- Bluetooth and USB devices can be speakers as well as headsets, so transport alone proves nothing.
Detection runs once at start; unplugging or switching output mid-session is not tracked.
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass

# letters on both sides break a match ("Buds2" matches, "Budsmith" does not); digits and punctuation do not
HEADPHONE_WORDS = re.compile(r"(?<![a-z])(head ?phones?|head ?set|airpods?|earbuds?|earphones?|buds|earpods?|in-?ears?)(?![a-z])", re.I)
SPEAKER_WORDS = re.compile(r"(?<![a-z])(speakers?|display|hdmi|tv|soundbar|homepod|boombox)(?![a-z])", re.I)


@dataclass(frozen=True)
class OutputInfo:
    name: str = ""
    source: str = ""        # macOS "Output Source" when available
    transport: str = ""     # Built-in, Bluetooth, USB, ...


@dataclass(frozen=True)
class OutputGuess:
    kind: str               # "headphones" | "speaker"
    confident: bool
    reason: str


def classify_output(info: OutputInfo) -> OutputGuess:
    """Headphones only when the evidence says so; everything else is treated as open speakers."""
    text = f"{info.source} {info.name}".strip()
    if not text:
        return OutputGuess("speaker", False, "no output device information; using the safe default")
    if HEADPHONE_WORDS.search(info.source):
        return OutputGuess("headphones", True, f"output source reports headphones ({info.source})")
    if SPEAKER_WORDS.search(info.source):
        return OutputGuess("speaker", True, f"output source is speakers ({info.source})")
    if HEADPHONE_WORDS.search(info.name):
        return OutputGuess("headphones", True, f"device name looks like headphones ({info.name})")
    if SPEAKER_WORDS.search(info.name):
        return OutputGuess("speaker", True, f"device name looks like speakers ({info.name})")
    return OutputGuess("speaker", False, f"cannot tell what '{info.name or info.source}' is; using the safe default")


def _parse_profiler(text: str) -> OutputInfo | None:
    """Pick the default output device block out of `system_profiler SPAudioDataType` text."""
    blocks = re.split(r"\n(?=        \S[^\n]*:\n)", text)
    for b in blocks:
        if "Default Output Device: Yes" not in b:
            continue
        head = b.strip().splitlines()[0].rstrip(":").strip()
        src = re.search(r"Output Source:\s*(.+)", b)
        tr = re.search(r"Transport:\s*(.+)", b)
        return OutputInfo(head, src.group(1).strip() if src else "", tr.group(1).strip() if tr else "")
    return None


def probe_output(timeout_s: float = 4.0) -> OutputInfo | None:
    """Ask the OS about the default output. macOS only; any failure returns None (never raises)."""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["system_profiler", "SPAudioDataType"], capture_output=True, text=True,
                                 timeout=timeout_s, check=False).stdout
            info = _parse_profiler(out)
            if info is not None:
                return info
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import sounddevice as sd
        return OutputInfo(name=str(sd.query_devices(kind="output")["name"]))
    except Exception:  # noqa: BLE001 - detection is optional; the caller falls back to the safe default
        return None


def detect_output() -> OutputGuess:
    info = probe_output()
    return classify_output(info) if info is not None else classify_output(OutputInfo())
