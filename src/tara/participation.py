"""Deterministic participation gate. The semantic intent is an *input* (from an
untrusted classifier); everything else is enforced here. See
docs/TURN_TAKING_AND_INTERRUPTION.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import CONTROL_ROLES, INTERVIEW_MODES, MeetingConfig, Role, SpeakingPolicy
from .runtime import MeetingRuntime, State


class IntentKind(str, Enum):
    ADDRESSED = "addressed_to_tara"
    INSTRUCTION = "instruction_to_tara"
    INVITATION = "invitation_to_contribute"
    ABOUT_TARA = "about_tara_not_to_her"
    HUMAN_TO_HUMAN = "human_to_human"
    SILENCE_REQUEST = "silence_request"
    TAKEOVER = "takeover"
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    confidence: float = 1.0
    is_direct_question: bool = False
    usefulness: float = 0.0
    in_scope: bool = True


@dataclass(frozen=True)
class Attribution:
    participant_id: str | None
    confidence: float
    overlap: bool = False


@dataclass(frozen=True)
class Utterance:
    event_id: str
    text: str
    attribution: Attribution
    pause_ms: int = 0


class Action(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    WAIT = "wait"
    ASK_CLARIFICATION = "ask_clarification"
    REFUSE = "refuse"
    TAKEOVER = "takeover"
    MUTE = "mute"


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    proactive: bool = False


@dataclass(frozen=True)
class Thresholds:
    """Calibrated from data later; values here are supplied by config/tests."""

    min_intent_confidence: float
    proactive_usefulness: float
    proactive_min_pause_ms: int
    default_attribution: float


def _role_of(participant_id: str | None, roles: dict[str, Role]) -> Role | None:
    return roles.get(participant_id) if participant_id else None


def decide(
    utt: Utterance,
    intent: Intent,
    rt: MeetingRuntime,
    cfg: MeetingConfig,
    roles: dict[str, Role],
    th: Thresholds,
) -> Decision:
    if rt.state in (State.STOPPED, State.RECOVERING, State.INITIALIZING, State.CONNECTING):
        return Decision(Action.DENY, "not_ready")

    min_conf = cfg.attribution_min_confidence or th.default_attribution
    identity_ok = (
        utt.attribution.participant_id is not None
        and utt.attribution.confidence >= min_conf
        and not utt.attribution.overlap
    )

    # Control intents require a trusted role AND reliable identity (prompt-injection guard).
    if intent.kind is IntentKind.TAKEOVER:
        if cfg.takeover_phrases_enabled and identity_ok and _role_of(utt.attribution.participant_id, roles) in CONTROL_ROLES:
            return Decision(Action.TAKEOVER, "takeover_phrase")
        return Decision(Action.DENY, "takeover_not_authorized")
    if intent.kind is IntentKind.SILENCE_REQUEST:
        return Decision(Action.MUTE, "asked_to_be_quiet")

    if rt.takeover_active:
        return Decision(Action.DENY, "takeover")
    if cfg.policy is SpeakingPolicy.SILENT or rt.policy_silent:
        return Decision(Action.DENY, "policy_silent")
    if rt.state is State.SPEAKING:
        return Decision(Action.DENY, "already_speaking")
    if rt.state is not State.EVALUATING_TURN:
        return Decision(Action.DENY, f"bad_state_{rt.state.value}")

    directed = intent.kind in (IntentKind.ADDRESSED, IntentKind.INSTRUCTION, IntentKind.INVITATION)
    if directed and intent.confidence < th.min_intent_confidence:
        return Decision(Action.DENY, "low_intent_confidence")

    proactive_ok = cfg.policy is SpeakingPolicy.PROACTIVE_ALLOWED and cfg.mode not in INTERVIEW_MODES
    if not directed:
        if (
            proactive_ok
            and intent.usefulness >= th.proactive_usefulness
            and utt.pause_ms >= th.proactive_min_pause_ms
            and not rt.others_speaking
        ):
            return Decision(Action.ALLOW, "proactive", proactive=True)
        return Decision(Action.DENY, "not_addressed")

    # Directed at Tara from here on.
    if cfg.mode in INTERVIEW_MODES and not identity_ok:
        return Decision(Action.ASK_CLARIFICATION, "speaker_identity_low")
    if not intent.in_scope:
        return Decision(Action.REFUSE, "out_of_scope")
    if rt.others_speaking and not intent.is_direct_question:
        return Decision(Action.WAIT, "floor_busy")
    return Decision(Action.ALLOW, "directed")
