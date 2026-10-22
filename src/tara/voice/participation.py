"""Participation control for the Live path.

Gemini Live will answer anything it hears, so whether Tara may be HEARD is decided here, deterministically, from
the input transcript, speaker attribution, meeting policy and persistent control state. A verdict can only
allow or deny delivery of audio or request an authorized control action; it never grants tools or widens
permissions. Spoken commands are recognized by `parse_command`, authorized by `control.policy`, and executed
by the engine: the model never decides them.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ..classify import TARA_NAMES, IntentClassifier, name_hint
from ..config import INTERVIEW_MODES, MeetingConfig, Role, SpeakingPolicy
from ..contracts import AdapterCapabilities
from ..control.commands import CommandKind, ControlCommand, parse_command
from ..control.policy import authorize
from ..control.state import ControlState
from ..participation import Attribution, Intent, IntentKind, Thresholds, identity_reliable, min_confidence, takeover_authorized


class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    TAKEOVER = "takeover"
    MUTE = "mute"          # "be quiet": persistent listen-only
    COMMAND = "command"    # an authorized spoken command for the engine to execute


@dataclass(frozen=True)
class GateDecision:
    verdict: Verdict
    reason: str
    command: ControlCommand | None = None


class LiveParticipation:
    def __init__(self, cfg: MeetingConfig, roles: dict[str, Role], classifier: IntentClassifier, th: Thresholds,
                 caps: AdapterCapabilities | None = None, identity_available: bool = False,
                 names: tuple[str, ...] = TARA_NAMES):
        self.cfg, self.roles, self.classifier, self.th = cfg, roles, classifier, th
        self.caps = caps or AdapterCapabilities()
        self.identity_available, self.names = identity_available, names
        self._intent_cache: tuple[str, Intent] | None = None
        self.follow_up_s = 0.0                         # after she answers, this long counts as "still talking to her" (0 = off)
        self.clock: Callable[[], float] = time.monotonic
        self._follow_until = 0.0

    def note_reply(self) -> None:
        """Called when Tara has just answered a person: the conversation with her is open for `follow_up_s`."""
        self._follow_until = self.clock() + self.follow_up_s if self.follow_up_s > 0 else 0.0

    async def _intent(self, text: str, context: list[str]) -> Intent:
        if self._intent_cache is not None and self._intent_cache[0] == text:
            return self._intent_cache[1]
        intent = await self.classifier.classify(text, context)
        self._intent_cache = (text, intent)
        return intent

    async def control(self, text: str, attribution: Attribution, *, state: ControlState | None = None,
                      tara_speaking: bool = False, context: list[str] | None = None) -> GateDecision | None:
        """Control intents (takeover, silence, spoken commands), independent of whether the model answers."""
        if not text.strip():
            return None
        cfg, state = self.cfg, state or ControlState()
        min_conf = min_confidence(cfg, self.th)
        cmd = parse_command(text, self.names)
        if cmd is not None:
            auth = authorize(cmd, cfg, self.roles, attribution, min_conf, state, self.caps, self.identity_available)
            if auth.ok:
                return GateDecision(Verdict.COMMAND, f"command:{cmd.kind.value}", cmd)
            return GateDecision(Verdict.DENY, f"command_denied:{cmd.kind.value}:{auth.reason}", cmd)
        intent = await self._intent(text, context or [])
        if intent.kind is IntentKind.TAKEOVER:
            if takeover_authorized(cfg, attribution, self.roles, min_conf):
                return GateDecision(Verdict.TAKEOVER, "takeover_phrase")
            return GateDecision(Verdict.DENY, "takeover_not_authorized")
        if intent.kind is IntentKind.SILENCE_REQUEST:
            addressed = name_hint(text, self.names)[0]
            if not addressed and not tara_speaking:
                return None                       # "be quiet" said to someone else must not silence Tara
            quiet = ControlCommand(CommandKind.LISTEN_ONLY)
            if not addressed:
                # Bare "be quiet" while Tara is talking is an interruption, not persistent control:
                # anyone may stop her talking now; only an authorized speaker may keep her silent.
                return GateDecision(Verdict.MUTE, "barge_in", quiet)
            auth = authorize(quiet, cfg, self.roles, attribution, min_conf, state, self.caps, self.identity_available)
            if auth.ok:
                return GateDecision(Verdict.MUTE, "asked_to_be_quiet", quiet)
            return GateDecision(Verdict.DENY, f"command_denied:listen_only:{auth.reason}", quiet)
        return None

    async def evaluate(self, text: str, attribution: Attribution, *, origin: str = "participant",
                       takeover_active: bool = False, muted: bool = False, context: list[str] | None = None,
                       control_state: ControlState | None = None, tara_speaking: bool = False) -> GateDecision:
        """origin: "participant" (reply to a human turn) or "app" (Tara-initiated, e.g. next interview question)."""
        cfg = self.cfg
        min_conf = min_confidence(cfg, self.th)
        if origin == "participant":
            ctl = await self.control(text, attribution, state=control_state, tara_speaking=tara_speaking, context=context)
            if ctl is not None:
                return ctl        # control intents, including denied ones, are never answered as if they were questions
        if cfg.policy is SpeakingPolicy.SILENT:
            return GateDecision(Verdict.DENY, "policy_silent")
        if muted or (control_state is not None and control_state.silenced):
            return GateDecision(Verdict.DENY, "muted")
        if takeover_active:
            return GateDecision(Verdict.DENY, "takeover")
        if origin == "app":
            return GateDecision(Verdict.ALLOW, "app_initiated")
        if (self.follow_up_s > 0 and self.clock() < self._follow_until and cfg.policy is SpeakingPolicy.ON_ADDRESS_ONLY
                and cfg.mode not in INTERVIEW_MODES):
            return GateDecision(Verdict.ALLOW, "follow_up")        # she was just talking to this room: no need to say her name again
        if cfg.wake_by_model and cfg.policy is SpeakingPolicy.ON_ADDRESS_ONLY and cfg.mode not in INTERVIEW_MODES:
            return GateDecision(Verdict.ALLOW, "model_wake")      # the model was told to answer only when called; trust its ears over the transcript
        if not text.strip():
            return GateDecision(Verdict.DENY, "no_transcript")     # fail silent when nothing is known about the turn
        intent = await self._intent(text, context or [])
        directed = intent.kind in (IntentKind.ADDRESSED, IntentKind.INSTRUCTION, IntentKind.INVITATION)
        if directed:
            if intent.confidence < self.th.min_intent_confidence:
                return GateDecision(Verdict.DENY, "low_intent_confidence")
            if cfg.mode in INTERVIEW_MODES and not identity_reliable(attribution, min_conf):
                return GateDecision(Verdict.DENY, "speaker_identity_low")
            return GateDecision(Verdict.ALLOW, "directed")
        if (cfg.lead_interview and cfg.mode in INTERVIEW_MODES
                and self.roles.get(attribution.participant_id or "") is Role.CANDIDATE):
            # Tara is running this interview: the candidate does not need to say her name to be answered.
            if not identity_reliable(attribution, min_conf):
                return GateDecision(Verdict.DENY, "speaker_identity_low")
            return GateDecision(Verdict.ALLOW, "candidate_turn")
        if cfg.policy is SpeakingPolicy.PROACTIVE_ALLOWED and cfg.mode not in INTERVIEW_MODES:
            return GateDecision(Verdict.ALLOW, "proactive")
        return GateDecision(Verdict.DENY, "not_addressed")
