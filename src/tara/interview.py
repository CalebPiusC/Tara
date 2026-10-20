"""Interview engine: question plan, round-robin turns, follow-up limits, evidence.

Evidence integrity: answers are stored only via EvidenceStore (attribution gate);
assessments may cite only event ids already in that candidate's evidence, so a
model cannot fabricate a quote or borrow another candidate's."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import MalformedOutput
from .evidence import EvidenceStore
from .participation import Utterance


@dataclass(frozen=True)
class Question:
    topic: str
    text: str


@dataclass(frozen=True)
class Assessment:
    candidate_id: str
    criterion: str
    kind: str  # "observation" | "interpretation"
    note: str
    evidence_event_ids: tuple[str, ...]
    score: int | None = None


@dataclass(frozen=True)
class AnswerQuality:
    completeness: float
    gaps: tuple[str, ...] = ()


def parse_quality(d: Any) -> AnswerQuality:
    if not isinstance(d, dict):
        raise MalformedOutput("quality not an object")
    try:
        c = float(d["completeness"])
    except (KeyError, TypeError, ValueError) as e:
        raise MalformedOutput("completeness missing") from e
    if not 0 <= c <= 1:
        raise MalformedOutput("completeness out of range")
    gaps = d.get("gaps") or []
    if not isinstance(gaps, list):
        raise MalformedOutput("gaps must be list")
    return AnswerQuality(c, tuple(map(str, gaps)))


class InterviewEngine:
    def __init__(self, questions: list[Question], candidate_ids: list[str], evidence: EvidenceStore,
                 max_followups: int = 2, complete_threshold: float = 0.7, round_robin: bool = True):
        if not questions:
            raise ValueError("need at least one question")
        if not candidate_ids:
            raise ValueError("need at least one candidate")
        self.questions, self.candidates, self.evidence = questions, candidate_ids, evidence
        self.max_followups, self.complete_threshold, self.round_robin = max_followups, complete_threshold, round_robin
        self.q_index = 0
        self._followups = 0
        self._answered: set[str] = set()
        self._order_offset = 0
        self.assessments: list[Assessment] = []
        self.unsolicited: list[Utterance] = []

    @property
    def current_question(self) -> Question | None:
        return self.questions[self.q_index] if self.q_index < len(self.questions) else None

    @property
    def done(self) -> bool:
        return self.q_index >= len(self.questions)

    def current_candidate(self) -> str | None:
        """Whose turn it is (round-robin rotates the starting candidate per question)."""
        if self.done:
            return None
        remaining = [c for c in self._rotated() if c not in self._answered]
        return remaining[0] if remaining else None

    def _rotated(self) -> list[str]:
        if not self.round_robin:
            return list(self.candidates)
        k = self._order_offset % len(self.candidates)
        return self.candidates[k:] + self.candidates[:k]

    def record_answer(self, u: Utterance) -> str:
        """Returns 'recorded' | 'unsolicited' | 'quarantined'. Never assigns by guess."""
        who = self.current_candidate()
        if u.attribution.participant_id is None:
            self.evidence.add(u)
            return "quarantined"
        stored = self.evidence.add(u)
        if not stored:
            return "quarantined"
        if u.attribution.participant_id != who:
            self.unsolicited.append(u)
            return "unsolicited"
        return "recorded"

    def after_answer(self, candidate_id: str, quality: AnswerQuality) -> str:
        """Decide next step: 'followup' | 'next_candidate' | 'next_question' | 'done'."""
        if quality.completeness < self.complete_threshold and self._followups < self.max_followups:
            self._followups += 1
            return "followup"
        self._answered.add(candidate_id)
        self._followups = 0
        if any(c not in self._answered for c in self.candidates):
            return "next_candidate"
        self.q_index += 1
        self._answered.clear()
        self._order_offset += 1
        return "done" if self.done else "next_question"

    def add_assessment(self, a: Assessment) -> None:
        if a.kind not in ("observation", "interpretation"):
            raise ValueError("kind must be observation or interpretation")
        owned = {e.event_id for e in self.evidence.report_for(a.candidate_id)}
        if a.kind == "observation" and not a.evidence_event_ids:
            raise ValueError("an observation must cite evidence")
        missing = [e for e in a.evidence_event_ids if e not in owned]
        if missing:
            raise ValueError(f"evidence not attributed to {a.candidate_id}: {missing}")
        self.assessments.append(a)
