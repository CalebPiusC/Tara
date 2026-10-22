"""BehaviourController: turns conversation signals into a small structured plan and, when the
situation changes, a short standing style note for the live model.

What it is NOT: a permission system. It has no reference to the participation gate, cannot allow or
deny speech, and its plan is advisory input for (a) style guidance, (b) expression hints, (c) metrics.
Runs synchronously on cheap lexical signals: no model call, no await, no added delay on the audio path.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from ..core.config import INTERVIEW_MODES, Mode
from .checks import Flag, flag_text, opener_of
from .meeting_kind import KIND_RULES, MeetingKind, MeetingKindTracker
from .profiles import EXPRESSIVENESS, HUMOUR, PROFILES, VERBOSITY, Profile, ProfileParams, clamp
from .signals import Signals, analyze

SERIOUS_REGIMES = frozenset({"sensitive", "serious", "empathy"})


@dataclass(frozen=True)
class BehaviourPlan:
    intent: str          # answer | accept_correction | clarify_simply | empathize_then_help | react_lightly | acknowledge_briefly | stay_back
    tone: str            # neutral | warm | gentle | serious | playful
    expressiveness: str  # none | subtle | moderate | animated
    humour: str          # none | restrained | light | playful
    laughter: bool       # a brief verbal amusement reaction would fit (advisory)
    verbosity: str       # brief | concise | detailed
    formality: str       # formal | neutral | casual
    regime: str          # neutral | playful | empathy | serious | sensitive
    reasons: tuple[str, ...] = ()
    kind: str = ""       # inferred or configured meeting kind; "" = none assumed


@dataclass
class _GuidanceKey:
    regime: str
    verbosity: str
    expressiveness: str
    avoid: tuple[str, ...]
    kind: str = ""


class BehaviourController:
    def __init__(self, profile: Profile, mode: Mode, names: tuple[str, ...] = ("tara",), window: int = 8,
                 serious_hold_turns: int = 6, playful_hold_turns: int = 4, interrupted_hold_turns: int = 2,
                 empathy_hold_turns: int = 3, kinds: MeetingKindTracker | None = None,
                 guidance_min_interval_s: float = 15.0, clock: Callable[[], float] = time.monotonic):
        self.profile = profile
        self.params: ProfileParams = PROFILES[profile]
        self.mode, self.names, self._clock = mode, names, clock
        self.interview = mode in INTERVIEW_MODES
        self.history: deque[Signals] = deque(maxlen=window)
        self.turn = 0
        self.serious_hold, self.playful_hold, self.interrupted_hold = serious_hold_turns, playful_hold_turns, interrupted_hold_turns
        self.empathy_hold = empathy_hold_turns
        self.kinds = kinds or MeetingKindTracker("", mode)
        self.min_interval = guidance_min_interval_s
        self._serious_until = -1
        self._sensitive_until = -1
        self._empathy_until = -1
        self._playful_until = -1
        self._interrupted_until = -1
        self._pressure_until = -1
        self.pressure = 0
        self.yielded = False
        self.muted = False
        self._last_laugh_t = -1e9
        self._last_laugh_turn = -10**6
        self._laugh_planned = False
        self.openers: deque[str] = deque(maxlen=6)
        self.flag_counts: dict[str, int] = {}
        self._key: _GuidanceKey | None = None
        self._key_t = -1e9
        self.last_plan: BehaviourPlan | None = None
        self.last_signals = Signals()
        self.last_question = ""

    # ---- observation ----------------------------------------------------------------------------
    def observe(self, text: str, final: bool = False) -> BehaviourPlan:
        """Update from a participant turn. `final=False` while the turn is still being transcribed
        (nothing persists); `final=True` when the turn is complete (rolling context is updated)."""
        sig = analyze(text, self.names)
        self.last_signals = sig
        if sig.is_question:
            self.last_question = text
        if final:
            self.history.append(sig)
            self.turn += 1
            self.kinds.observe(text, urgent=sig.urgency)
            if sig.sensitive:
                self._sensitive_until = self.turn + self.serious_hold + 2
            if sig.urgency or sig.conflict:
                self._serious_until = self.turn + self.serious_hold
            if sig.bad_news or sig.frustration:
                self._empathy_until = self.turn + self.empathy_hold
            if sig.playful and not sig.heavy:
                self._playful_until = self.turn + self.playful_hold
        self.last_plan = self._plan(sig)
        self._laugh_planned = self.last_plan.laughter
        return self.last_plan

    def _recent(self, attr: str, n: int = 3) -> bool:
        return any(getattr(s, attr) for s in list(self.history)[-n:])

    def _plan(self, sig: Signals) -> BehaviourPlan:
        p, reasons = self.params, []
        t = self.turn
        sensitive = sig.sensitive or t < self._sensitive_until
        serious = sig.urgency or sig.conflict or t < self._serious_until
        empathy = sig.frustration or sig.bad_news or t < self._empathy_until
        if sensitive:
            regime = "sensitive"
        elif serious:
            regime = "serious"
        elif empathy:
            regime = "empathy"
        elif (sig.playful or t < self._playful_until or self._recent("playful", 3)):
            regime = "playful"
        else:
            regime = "neutral"
        reasons.append(f"regime:{regime}")

        # humour: never in heavy regimes, interviews, or while yielding; needs a playful room
        humour = "none"
        if regime == "playful" and not self.interview and not self.yielded and not self.muted:
            humour = clamp("playful", HUMOUR, p.max_humour)
            if p.humour_needs_playful_context and not (sig.playful or t < self._playful_until or self._recent("playful", 3)):
                humour = "none"
        else:
            reasons.append("humour_blocked")
        kind = self.kinds.kind
        rules = KIND_RULES[kind] if kind is not None else None
        if kind is not None:
            rules = KIND_RULES[kind]
            if kind is MeetingKind.INCIDENT and regime in ("neutral", "playful"):
                regime = "serious"
                reasons[0] = "regime:serious"
            if rules.humour_cap is not None and HUMOUR.index(humour) > HUMOUR.index(rules.humour_cap):
                humour = rules.humour_cap
                reasons.append(f"humour_capped_by_kind:{kind.value}")
            if rules.relax_humour_cue and humour == "none" and regime == "neutral" and not self.interview \
                    and not self.yielded and not self.muted and p.max_humour in ("light", "playful"):
                humour = "light"                    # brainstorming may be lighter without a joke cue; laughter still needs one
                reasons.append("brainstorm_light_humour")
        now = self._clock()
        cooldown_ok = (now - self._last_laugh_t >= p.laughter_cooldown_s) and (t - self._last_laugh_turn >= p.laughter_cooldown_turns)
        laughter = bool(sig.playful and humour != "none" and cooldown_ok)
        if sig.playful and humour != "none" and not cooldown_ok:
            reasons.append("laughter_cooldown")

        base_x = p.default_expressiveness
        if regime in SERIOUS_REGIMES or sig.urgency:
            expressiveness = clamp(base_x, EXPRESSIVENESS, "subtle")
        elif regime == "playful" and humour != "none":
            expressiveness = p.max_expressiveness
        else:
            expressiveness = base_x
        expressiveness = clamp(expressiveness, EXPRESSIVENESS, p.max_expressiveness)
        if rules is not None and rules.expressiveness_cap is not None:
            expressiveness = clamp(expressiveness, EXPRESSIVENESS, rules.expressiveness_cap)

        # verbosity: start from profile, move down for urgency / interruptions / feedback, up on request
        level = VERBOSITY.index(p.default_verbosity)
        if sig.asks_detail and not sig.urgency:
            level = VERBOSITY.index("detailed")
            reasons.append("asked_detail")
        else:
            if sig.urgency or sig.asks_brief:
                level = 0
            if t < self._interrupted_until:
                level = min(level, 0)
                reasons.append("recently_interrupted")
            if sig.is_question and sig.words <= 8:
                level = max(0, level - 1)
            if t < self._pressure_until and self.pressure:
                level = max(0, level - self.pressure)
                reasons.append("verbosity_feedback")
        verbosity = VERBOSITY[level]

        formality = "formal" if self.interview else (sig.formality if sig.formality != "neutral" else p.default_formality)
        if rules is not None and rules.formality == "formal":
            formality = "formal"
        tone = {"sensitive": "gentle", "empathy": "gentle", "serious": "serious"}.get(regime)
        if tone is None:
            tone = "playful" if (regime == "playful" and humour != "none") else p.default_tone

        if self.yielded:
            intent = "stay_back"
        elif sig.correction:
            intent = "accept_correction"
        elif sig.confusion:
            intent = "clarify_simply"
        elif sensitive or empathy:
            intent = "empathize_then_help"
        elif laughter:
            intent = "react_lightly"
        elif sig.is_question or sig.addressed_by_name:
            intent = "answer"
        else:
            intent = "acknowledge_briefly"
        if humour == "none" and tone == "playful":
            tone = p.default_tone
        return BehaviourPlan(intent, tone, expressiveness, humour, laughter, verbosity, formality, regime, tuple(reasons),
                             kind.value if kind is not None else "")

    def refresh(self) -> BehaviourPlan:
        """Re-plan from the latest signals (after feedback such as a too-long answer)."""
        self.last_plan = self._plan(self.last_signals)
        return self.last_plan

    # ---- events from the runtime -----------------------------------------------------------------
    def note_interrupted(self) -> None:
        self._interrupted_until = self.turn + self.interrupted_hold

    def note_yield(self, yielded: bool) -> None:
        self.yielded = yielded

    def note_muted(self, muted: bool) -> None:
        self.muted = muted

    def note_response_started(self) -> None:
        """Tara is actually speaking: spend the laughter allowance if the plan offered one."""
        if self._laugh_planned:
            self._last_laugh_t, self._last_laugh_turn = self._clock(), self.turn
            self._laugh_planned = False

    def note_response(self, text: str) -> list[Flag]:
        """Check what Tara said; feed repetition and verbosity feedback. Heuristic and English only."""
        plan = self.last_plan
        flags = flag_text(text, plan.verbosity if plan else "concise", self.last_question,
                          laughter_allowed=bool(plan and plan.laughter), recent_openers=tuple(self.openers))
        op = opener_of(text)
        if op:
            self.openers.append(op)
        for f in flags:
            self.flag_counts[f.name] = self.flag_counts.get(f.name, 0) + 1
        if any(f.name == "overlong" for f in flags):
            self.pressure = min(2, self.pressure + 1)
            self._pressure_until = self.turn + 3
        elif self.pressure and self.turn >= self._pressure_until:
            self.pressure = 0
        return flags

    # ---- standing guidance for the live model --------------------------------------------------------
    def _avoid(self) -> tuple[str, ...]:
        counts: dict[str, int] = {}
        for o in self.openers:
            counts[o] = counts.get(o, 0) + 1
        repeated = sorted(o for o, c in counts.items() if c >= 2)
        return tuple(repeated[:2])

    def guidance(self) -> str | None:
        """A short note to add to the live session when the situation changed. Rate-limited, except that
        moving into a serious or sensitive moment is never delayed (humour there does harm)."""
        plan = self.last_plan
        if plan is None:
            return None
        key = _GuidanceKey(plan.regime, plan.verbosity, plan.expressiveness, self._avoid(), plan.kind)
        prev = self._key
        if prev is not None and (prev.regime, prev.verbosity, prev.expressiveness, prev.avoid, prev.kind) == (
                key.regime, key.verbosity, key.expressiveness, key.avoid, key.kind):
            return None
        now = self._clock()
        entering_heavy = plan.regime in SERIOUS_REGIMES and (prev is None or prev.regime not in SERIOUS_REGIMES)
        if prev is None and plan.regime == "neutral" and plan.verbosity == self.params.default_verbosity and not key.avoid \
                and not plan.kind:
            self._key = key                                  # nothing to say about a calm default state
            return None
        if not entering_heavy and now - self._key_t < self.min_interval:
            return None
        self._key, self._key_t = key, now
        return render_guidance(plan, key.avoid)


_REGIME_TEXT = {
    "sensitive": "a sensitive topic came up: be gentle, no humour, don't rush to fix things",
    "serious": "the mood is serious: no humour, be direct and brief",
    "empathy": "someone sounds frustrated or let down: acknowledge it in a sentence first, then help",
    "playful": "the room is playful: a brief light reaction is fine if it genuinely fits",
    "neutral": "back to a steady, normal tone",
}
_VERBOSITY_TEXT = {"brief": "keep replies brief", "concise": "keep replies concise", "detailed": "more detail is welcome if asked"}


def render_guidance(plan: BehaviourPlan, avoid: tuple[str, ...] = ()) -> str:
    parts = [_REGIME_TEXT[plan.regime], _VERBOSITY_TEXT[plan.verbosity]]
    if plan.kind:
        parts.insert(0, KIND_RULES[MeetingKind(plan.kind)].guidance)
    if plan.expressiveness in ("none", "subtle"):
        parts.append("keep emotional expression subtle")
    if avoid:
        parts.append("vary how you open replies (recently overused: " + "; ".join(avoid) + ")")
    return "Style update for your next replies. Internal note: do not speak it and do not answer it: " + "; ".join(parts) + "."
