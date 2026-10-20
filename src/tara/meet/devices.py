"""Virtual audio devices for the Meet bot (the route other meeting bots use).

Meeting audio: Chrome plays Meet to one virtual cable (default "BlackHole 2ch"); Tara reads that cable as an input.
Tara's voice: Tara plays to a second virtual cable (default "BlackHole 16ch"); Chrome's microphone is that cable.
Two cables, never one, because a single loopback would feed the meeting back into itself. Neither cable is the system default, so
the rest of the computer keeps its normal sound.

Install (macOS, needs your password once): `brew install blackhole-2ch blackhole-16ch`, then restart the Mac or run
`sudo killall coreaudiod` so the devices appear. Linux servers use PulseAudio null sinks instead (not built here).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

INSTALL_HELP = ("Install the two virtual cables with `brew install blackhole-2ch blackhole-16ch` (it asks for your password), then "
                "restart the Mac, or run `sudo killall coreaudiod`, and check with `./dev.sh meet check`.")


@dataclass(frozen=True)
class VirtualAudio:
    meeting_in: str = "BlackHole 2ch"       # Chrome's speaker; Tara listens here
    voice_out: str = "BlackHole 16ch"       # Tara's voice goes here; Chrome's microphone listens here


def list_audio_devices(sd: Any | None = None) -> list[dict[str, Any]]:
    if sd is None:
        import sounddevice
        sd = sounddevice
    return [dict(d) for d in sd.query_devices()]


def check_devices(cfg: VirtualAudio, sd: Any | None = None) -> list[tuple[str, bool, str]]:
    """What is present: [(what, ok, note)]. Never raises."""
    try:
        devs = list_audio_devices(sd)
    except Exception as exc:  # noqa: BLE001
        return [("audio devices", False, f"could not list audio devices ({type(exc).__name__})")]

    def has(name: str, key: str) -> bool:
        return any(name.lower() in str(d["name"]).lower() and int(d[key]) > 0 for d in devs)

    return [
        (f"meeting audio cable '{cfg.meeting_in}'", has(cfg.meeting_in, "max_input_channels") and has(cfg.meeting_in, "max_output_channels"),
         "Chrome's speaker, read by Tara"),
        (f"voice cable '{cfg.voice_out}'", has(cfg.voice_out, "max_input_channels") and has(cfg.voice_out, "max_output_channels"),
         "Tara's voice, Chrome's microphone"),
    ]
