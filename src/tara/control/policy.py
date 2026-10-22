"""Who may do what. Pure function of role, identity reliability, meeting mode, current state and what the
platform adapter can really do."""
from __future__ import annotations

from dataclasses import dataclass

from ..core.config import CONTROL_ROLES, INTERVIEW_MODES, MeetingConfig, Role
from ..core.contracts import AdapterCapabilities
from ..floor.participation import Attribution, identity_reliable
from .commands import CommandKind, ControlCommand
from .state import ControlState

# anyone: reduces Tara's footprint (safe direction). control: expands it or affects the meeting.
LEVEL = {
    CommandKind.MIC_MUTE: "anyone", CommandKind.LISTEN_ONLY: "anyone", CommandKind.SHOW_IDENTITY: "anyone",
    CommandKind.RESUME: "resume", CommandKind.LEAVE: "control", CommandKind.CAMERA_ON: "control",
    CommandKind.CAMERA_OFF: "control", CommandKind.NOTES_ON: "control", CommandKind.NOTES_OFF: "control",
    CommandKind.CONFIRM_NOTE: "control", CommandKind.MUTE_OTHER: "control",
}
DEFAULT_ENABLED = frozenset(k for k in CommandKind if k is not CommandKind.MUTE_OTHER)


@dataclass(frozen=True)
class Authorization:
    ok: bool
    reason: str = "ok"


def authorize(cmd: ControlCommand, cfg: MeetingConfig, roles: dict[str, Role], att: Attribution, min_conf: float,
              state: ControlState, caps: AdapterCapabilities, identity_available: bool = False) -> Authorization:
    enabled = {CommandKind(c) for c in cfg.allowed_commands} if cfg.allowed_commands else DEFAULT_ENABLED
    if cmd.kind not in enabled:
        return Authorization(False, "not_enabled")
    # platform reality first: never promise what the adapter cannot do
    if cmd.kind in (CommandKind.CAMERA_ON, CommandKind.CAMERA_OFF) and not caps.can_camera:
        return Authorization(False, "unsupported")
    if cmd.kind is CommandKind.SHOW_IDENTITY and not (caps.can_show_image and identity_available):
        return Authorization(False, "unsupported")
    if cmd.kind is CommandKind.MUTE_OTHER and not caps.can_mute_others:
        return Authorization(False, "unsupported")
    level = LEVEL[cmd.kind]
    if level == "anyone" and cfg.mode in INTERVIEW_MODES:
        level = "control"            # in interviews a candidate must not be able to silence the interviewer
    reliable = identity_reliable(att, min_conf)
    is_control = reliable and roles.get(att.participant_id or "") in CONTROL_ROLES
    if level == "anyone":
        return Authorization(True)
    if level == "resume":
        if is_control:
            return Authorization(True)
        if reliable and state.set_by is not None and att.participant_id == state.set_by:
            return Authorization(True, "muter_may_resume")
        return Authorization(False, "identity_low" if not reliable else "needs_control_role")
    if not reliable:
        return Authorization(False, "identity_low")
    return Authorization(True) if is_control else Authorization(False, "needs_control_role")
