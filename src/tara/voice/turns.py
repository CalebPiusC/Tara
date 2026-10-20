from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurnRecord:
    """A finished participant turn, as seen by observers. `reliable` means identity passed the meeting's threshold."""

    text: str
    speaker_id: str | None
    speaker_name: str | None
    confidence: float
    reliable: bool
    turn_id: str
