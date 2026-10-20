from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ControlState:
    """Persistent runtime state set by spoken commands. Survives across turns and reconnects."""

    speech_mode: str = "active"          # active | listen_only | muted
    set_by: str | None = None            # participant who silenced Tara (may resume her)
    notes_active: bool = False
    camera_on: bool | None = None        # None = unknown / unsupported
    identity_shown: bool = False
    left: bool = False

    @property
    def silenced(self) -> bool:
        return self.speech_mode != "active"
