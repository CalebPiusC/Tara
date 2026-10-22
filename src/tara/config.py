"""MeetingConfig (schema tara.meeting_config/v0) and deterministic validation.

Natural-language extraction is NOT here: this module validates a structured draft.
See docs/MEETING_CONTEXT_MODEL.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

SCHEMA = "tara.meeting_config/v0"
KIND_NAMES = frozenset({"casual", "technical", "interview", "client", "incident", "brainstorm"})   # mirrors behaviour.meeting_kind (tested)
COMMAND_NAMES = frozenset({"mic_mute", "listen_only", "resume", "leave", "camera_on", "camera_off", "show_identity", "notes_on", "notes_off", "confirm_note", "mute_other"})   # mirrors control.commands (tested)
PROFILE_NAMES = frozenset({"professional", "conversational", "expressive"})   # mirrors behaviour.profiles.Profile (tested)


class Mode(str, Enum):
    INTERVIEW_STRUCTURED = "interview_structured"
    INTERVIEW_CONVERSATIONAL = "interview_conversational"
    GROUP_INTERVIEW = "group_interview"
    NATURAL = "natural"
    PITCH = "pitch"
    OBSERVE = "observe"


INTERVIEW_MODES = {Mode.INTERVIEW_STRUCTURED, Mode.INTERVIEW_CONVERSATIONAL, Mode.GROUP_INTERVIEW}


class SpeakingPolicy(str, Enum):
    ON_ADDRESS_ONLY = "on_address_only"
    MODERATED = "moderated"
    PROACTIVE_ALLOWED = "proactive_allowed"
    SILENT = "silent"


class Role(str, Enum):
    CANDIDATE = "candidate"
    ORGANIZER = "organizer"
    RECRUITER = "recruiter"
    ENGINEER = "engineer"
    CUSTOMER = "customer"
    OBSERVER = "observer"


# Roles whose takeover phrases / policy-changing instructions Tara may honor.
CONTROL_ROLES = {Role.ORGANIZER, Role.RECRUITER}


@dataclass(frozen=True)
class ParticipantSpec:
    ref: str
    display_name: str
    role: Role
    expected: bool = True


@dataclass(frozen=True)
class ToolSpec:
    name: str
    scope: str
    requires_human_approval: bool = True


@dataclass(frozen=True)
class MeetingConfig:
    purpose: str
    mode: Mode
    duration_min: int
    policy: SpeakingPolicy
    participants: tuple[ParticipantSpec, ...] = ()
    takeover_phrases_enabled: bool = True
    attribution_min_confidence: float | None = None
    individual_reports: bool = False
    tools: tuple[ToolSpec, ...] = ()
    disclosure_text: str = ""
    send_transcript_to_llm: bool = True  # False forbids cloud escalation
    behaviour_profile: str = ""          # "" = use the runtime default; style only, never permissions
    meeting_kind: str = ""               # "" = infer from the conversation; otherwise an explicit override (style only)
    allowed_commands: tuple[str, ...] = ()  # spoken-command allow-list; empty = defaults (everything except mute_other)
    retain_audio: bool = False
    approved_knowledge_ids: tuple[str, ...] = ()
    no_commitments: tuple[str, ...] = ()
    wake_by_model: bool = False          # standby: the model itself judges whether it was called by name (the transcript often misses the name)
    lead_interview: bool = False         # Tara leads: a candidate's turn (reliable identity) is always answered; style of a 1:1 interview
    schema: str = SCHEMA


@dataclass(frozen=True)
class AppPolicy:
    """Maximum capabilities set in code/config, never by organizer prose."""

    allowed_tools: frozenset[str] = frozenset()
    allow_proactive: bool = True
    allow_audio_retention: bool = False


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" blocks start; "warning" does not
    field: str
    message: str


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)
    effective_tools: tuple[ToolSpec, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


def validate(cfg: MeetingConfig, policy: AppPolicy) -> ValidationResult:
    res = ValidationResult()

    def err(f: str, m: str) -> None:
        res.issues.append(Issue("error", f, m))

    def warn(f: str, m: str) -> None:
        res.issues.append(Issue("warning", f, m))

    if cfg.schema != SCHEMA:
        err("schema", f"unsupported schema {cfg.schema!r}")
    if not cfg.purpose.strip():
        err("purpose", "missing")
    if cfg.duration_min <= 0:
        err("duration_min", "must be positive")
    if not cfg.disclosure_text.strip():
        err("disclosure_text", "AI-presence disclosure is required")

    refs = [p.ref for p in cfg.participants]
    if len(set(refs)) != len(refs):
        err("participants", "duplicate participant ref")
    names = [p.display_name.strip().lower() for p in cfg.participants]
    if len(set(names)) != len(names):
        warn("participants", "duplicate display names make identity ambiguous")

    candidates = [p for p in cfg.participants if p.role is Role.CANDIDATE]
    if cfg.mode in INTERVIEW_MODES:
        if not candidates:
            err("participants", "interview requires at least one candidate")
        if not any(p.role in CONTROL_ROLES for p in cfg.participants):
            warn("participants", "no organizer/recruiter present; takeover cannot be honored")
    if cfg.mode is Mode.GROUP_INTERVIEW and len(candidates) < 2:
        err("participants", "group interview requires at least two candidates")

    multi = len(candidates) > 1
    if cfg.individual_reports and multi and cfg.attribution_min_confidence is None:
        err("attribution_min_confidence", "required when multiple candidates get individual reports")
    t = cfg.attribution_min_confidence
    if t is not None and not (0.0 < t <= 1.0):
        err("attribution_min_confidence", "must be in (0, 1]")

    if cfg.mode is Mode.OBSERVE and cfg.policy is not SpeakingPolicy.SILENT:
        err("policy", "observe mode requires silent policy")
    if cfg.policy is SpeakingPolicy.PROACTIVE_ALLOWED and not policy.allow_proactive:
        err("policy", "proactive speech not permitted by app policy")
    if cfg.mode in INTERVIEW_MODES and cfg.policy is SpeakingPolicy.PROACTIVE_ALLOWED:
        err("policy", "proactive speech contradicts interview turn rules")

    if cfg.behaviour_profile and cfg.behaviour_profile not in PROFILE_NAMES:
        err("behaviour_profile", f"unknown profile {cfg.behaviour_profile!r}; choose from {sorted(PROFILE_NAMES)}")
    elif cfg.behaviour_profile and cfg.behaviour_profile != "professional" and cfg.mode in INTERVIEW_MODES:
        warn("behaviour_profile", "interviews always use the professional profile; the requested one is ignored")
    if cfg.meeting_kind and cfg.meeting_kind not in KIND_NAMES:
        err("meeting_kind", f"unknown kind {cfg.meeting_kind!r}; choose from {sorted(KIND_NAMES)}")
    bad_cmds = [c for c in cfg.allowed_commands if c not in COMMAND_NAMES]
    if bad_cmds:
        err("allowed_commands", f"unknown commands {bad_cmds}")
    if "mute_other" in cfg.allowed_commands:
        warn("allowed_commands", "mute_other also needs a platform adapter that supports muting others; it is off by default")
    if cfg.retain_audio and not policy.allow_audio_retention:
        err("retain_audio", "audio retention not permitted by app policy")

    if cfg.mode is Mode.PITCH and not cfg.approved_knowledge_ids:
        err("knowledge", "pitch mode requires approved knowledge sources")

    # Effective tools = requested ∩ app policy; prose can never add a tool.
    allowed = []
    for tool in cfg.tools:
        if tool.name in policy.allowed_tools:
            allowed.append(tool)
        else:
            warn("tools", f"tool {tool.name!r} not permitted by app policy; dropped")
    res.effective_tools = tuple(allowed)
    return res
