"""Meeting-kind awareness: infer what sort of meeting this is from the organizer's setup and the conversation,
with an explicit override. It only adjusts STYLE within the active profile's ceilings (it can restrict humour or
formality, or relax the need for a playful cue in brainstorming); it never touches permissions.

Inference is lexical and English-only, needs sustained evidence to switch (hysteresis), and prefers 'unknown'
(no adjustment) over a wrong guess: it must not assume every meeting is informal.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from enum import Enum

from ..core.config import INTERVIEW_MODES, Mode


class MeetingKind(str, Enum):
    CASUAL = "casual"
    TECHNICAL = "technical"
    INTERVIEW = "interview"
    CLIENT = "client"
    INCIDENT = "incident"
    BRAINSTORM = "brainstorm"


@dataclass(frozen=True)
class KindRules:
    humour_cap: str | None        # ceiling that can only lower the profile's humour
    formality: str | None
    expressiveness_cap: str | None
    relax_humour_cue: bool        # allow light humour without a participant joke (never laughter)
    guidance: str                 # one clause for the style note; descriptors, not scripted lines


KIND_RULES: dict[MeetingKind, KindRules] = {
    MeetingKind.CASUAL: KindRules(None, None, None, False, "this is a relaxed conversation"),
    MeetingKind.TECHNICAL: KindRules("restrained", None, "moderate", False,
                                     "this is a technical discussion: be precise and willing to discuss trade-offs"),
    MeetingKind.INTERVIEW: KindRules("none", "formal", "subtle", False, "this is an interview: stay professional, attentive and concise"),
    MeetingKind.CLIENT: KindRules("restrained", "formal", "moderate", False, "this is a client meeting: be polished and context-aware"),
    MeetingKind.INCIDENT: KindRules("none", None, "subtle", False, "this is an incident: stay calm and direct, no jokes"),
    MeetingKind.BRAINSTORM: KindRules(None, None, None, True, "this is a brainstorm: offer ideas freely and label them as ideas"),
}

_W = lambda words: re.compile(r"\b(" + "|".join(words) + r")\b", re.I)  # noqa: E731
_VOCAB: dict[MeetingKind, re.Pattern[str]] = {
    MeetingKind.TECHNICAL: _W(["api", "endpoint", "latency", "deploy(ment)?", "schema", "database", "migration", "kubernetes", "docker",
                               "cache", "redis", "postgres", "regression", "refactor", "bug", "throughput", "scaling", "architecture",
                               "repo", "pipeline", "ci", "build", "tests?", "rollback", "timeouts?", "queue", "thread", "memory leak",
                               "idempoten\\w+", "index(es)?", "query", "service", "latencies"]),
    MeetingKind.CLIENT: _W(["contract", "pricing", "proposal", "quote", "renewal", "invoice", "customers?", "clients?", "onboarding",
                            "demo", "roadmap", "sla", "budget", "stakeholders?", "deliverables?", "procurement", "terms"]),
    MeetingKind.BRAINSTORM: _W(["brainstorm", "ideas?", "what if", "imagine", "we could", "wild", "explore", "concept", "riff", "sketch",
                                "how about", "maybe we", "possibilit\\w+"]),
    MeetingKind.CASUAL: _W(["weekend", "lunch", "coffee", "birthday", "holiday", "weather", "game", "movie", "catch up", "how was",
                            "vacation", "dinner", "haha", "lol", "gonna", "wanna"]),
}


class MeetingKindTracker:
    def __init__(self, override: str = "", mode: Mode = Mode.NATURAL, window: int = 8, margin: float = 1.5,
                 switch_after: int = 2, cooldown_turns: int = 4):
        self.override = MeetingKind(override) if override else None
        self.mode = mode
        self.window, self.margin, self.switch_after, self.cooldown = window, margin, switch_after, cooldown_turns
        self._turns: deque[dict[MeetingKind, float]] = deque(maxlen=window)
        self._kind: MeetingKind | None = None
        self._lead_kind: MeetingKind | None = None
        self._lead_count = 0
        self._since_switch = 10**6
        self._incident_turns = 0

    @property
    def kind(self) -> MeetingKind | None:
        """None means 'no adjustment': nothing is assumed without evidence."""
        if self.override is not None:
            return self.override
        if self.mode in INTERVIEW_MODES:
            return MeetingKind.INTERVIEW
        if self._incident_turns > 0:
            return MeetingKind.INCIDENT
        if self._kind is not None:
            return self._kind
        if self.mode is Mode.PITCH:
            return MeetingKind.CLIENT
        return None

    def set_override(self, kind: str | None) -> None:
        self.override = MeetingKind(kind) if kind else None

    def observe(self, text: str, urgent: bool = False) -> MeetingKind | None:
        self._since_switch += 1
        self._incident_turns = 3 if urgent else max(0, self._incident_turns - 1)
        scores = {k: float(len(rx.findall(text))) for k, rx in _VOCAB.items()}
        self._turns.append(scores)
        total = {k: sum(t[k] for t in self._turns) for k in _VOCAB}
        ranked = sorted(total.items(), key=lambda kv: -kv[1])
        (top, s1), (_, s2) = ranked[0], ranked[1]
        if s1 >= 2 and s1 >= s2 + self.margin:
            if top is self._lead_kind:
                self._lead_count += 1
            else:
                self._lead_kind, self._lead_count = top, 1
            if (self._lead_count >= self.switch_after and top is not self._kind
                    and self._since_switch >= self.cooldown):
                self._kind, self._since_switch = top, 0
        else:
            self._lead_kind, self._lead_count = None, 0
        return self.kind
