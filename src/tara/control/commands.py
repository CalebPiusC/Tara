"""Spoken command grammar (English, deterministic). High precision on purpose: Tara must be addressed by name
near the start of the sentence, so talking ABOUT muting or leaving never triggers an action."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ..classify import name_hint

_WORD = re.compile(r"[a-z']+")
# A name is an address only at the start or after one of these vocatives. "yesterday Tara said ..." is
# speech ABOUT Tara, not a command to her.
_ADDRESS_PREFIXES = frozenset({"hey", "hi", "hello", "ok", "okay", "so", "um", "uh"})


class CommandKind(str, Enum):
    MIC_MUTE = "mic_mute"            # Tara's outgoing audio: closed in the app and, if supported, muted on the platform
    LISTEN_ONLY = "listen_only"      # stay quiet but keep listening (and noting)
    RESUME = "resume"                # speak again
    LEAVE = "leave"
    CAMERA_ON = "camera_on"
    CAMERA_OFF = "camera_off"
    SHOW_IDENTITY = "show_identity"  # show Tara's configured picture, if the platform can
    NOTES_ON = "notes_on"
    NOTES_OFF = "notes_off"
    CONFIRM_NOTE = "confirm_note"    # promote the latest candidate decision/action to confirmed
    MUTE_OTHER = "mute_other"        # muting another participant: never allowed unless explicitly enabled AND supported


@dataclass(frozen=True)
class ControlCommand:
    kind: CommandKind
    target: str = ""


_R = lambda p: re.compile(p, re.I)  # noqa: E731
_RULES: list[tuple[CommandKind, re.Pattern[str]]] = [
    (CommandKind.MUTE_OTHER, _R(r"\bmute (?!your|yourself|ur\b|the (mic|microphone)\b)(?!.*\byourself\b)([a-z][\w'-]*)")),
    (CommandKind.MIC_MUTE, _R(r"\bmute (yourself|your (mic|microphone|audio|sound)|ur mic)\b|\bturn (off|down) your (mic|microphone)\b")),
    (CommandKind.RESUME, _R(r"\bunmute( yourself| your (mic|microphone))?\b|\byou can (speak|talk|unmute)\b|\b(speak|talk) again\b|\bgo ahead and (speak|talk)\b|\byou may (speak|talk)\b|\bturn on your (mic|microphone)\b")),
    (CommandKind.LEAVE, _R(r"\b(leave|exit) (the )?(meeting|call|room)\b|\bdrop off( the (call|meeting))?\b|\bhang up\b|\byou can (go|leave|drop off)\b")),
    (CommandKind.CAMERA_OFF, _R(r"\b(turn|switch) off your camera\b|\b(turn|switch) your camera off\b|\bcamera off\b|\bstop (showing|sharing) (your )?(camera|video)\b")),
    (CommandKind.CAMERA_ON, _R(r"\b(turn|switch) on your camera\b|\b(turn|switch) your camera on\b|\bcamera on\b|\bshow (us )?your (face|camera)\b")),
    (CommandKind.SHOW_IDENTITY, _R(r"\bcan we see you\b|\bshow (us )?(yourself|what you look like|your (picture|avatar|photo|image))\b|\bwhat do you look like\b")),
    (CommandKind.NOTES_OFF, _R(r"\bstop (taking )?notes\b|\bno more notes\b")),
    (CommandKind.NOTES_ON, _R(r"\b(take|start|keep|begin) (some )?notes\b|\bnote (this|that) down\b|\btake minutes\b")),
    (CommandKind.CONFIRM_NOTE, _R(r"\b(confirm|mark) (that|this|it)( as (decided|agreed|confirmed|done))?\b|\bthat'?s (confirmed|agreed|decided)\b|\bwe (all )?agreed\b.*\b(note|record|mark|log)\b")),
    (CommandKind.LISTEN_ONLY, _R(r"\b(stay|keep|be|remain) (quiet|silent)\b|\blisten only\b|\bjust listen\b|\bdon'?t (speak|talk)\b|\bhold off\b|\bstop talking\b|\bpipe down\b")),
]


def parse_command(text: str, names: tuple[str, ...] = ("tara",), max_name_position: int = 2) -> ControlCommand | None:
    """Return the command if Tara is addressed in the first words and a grammar rule matches."""
    found, idx = name_hint(text, names)
    if not found or idx > max_name_position:
        return None
    if any(tok not in _ADDRESS_PREFIXES for tok in _WORD.findall(text.lower())[:idx]):
        return None
    for kind, rx in _RULES:
        m = rx.search(text)
        if m:
            target = m.group(m.lastindex).lower() if kind is CommandKind.MUTE_OTHER and m.lastindex else ""
            return ControlCommand(kind, target)
    return None

