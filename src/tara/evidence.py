"""Interview evidence store enforcing the attribution invariant:
evidence(X) only contains utterances attributed to X with confidence >= threshold
and no overlap. Everything else is quarantined. See docs/PARTICIPANT_IDENTITY.md."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .participation import Utterance


@dataclass(frozen=True)
class EvidenceItem:
    candidate_id: str
    event_id: str
    quote: str


class EvidenceStore:
    def __init__(self, min_confidence: float, candidate_ids: frozenset[str]):
        if not 0.0 < min_confidence <= 1.0:
            raise ValueError("min_confidence must be in (0, 1]")
        self._min = min_confidence
        self._candidates = candidate_ids
        self._by_candidate: dict[str, list[EvidenceItem]] = defaultdict(list)
        self.quarantine: list[tuple[Utterance, str]] = []

    def add(self, u: Utterance) -> bool:
        a = u.attribution
        if a.participant_id is None:
            return self._quarantine(u, "unknown_speaker")
        if a.participant_id not in self._candidates:
            return self._quarantine(u, "not_a_candidate")
        if a.overlap:
            return self._quarantine(u, "overlap")
        if a.confidence < self._min:
            return self._quarantine(u, "low_confidence")
        self._by_candidate[a.participant_id].append(EvidenceItem(a.participant_id, u.event_id, u.text))
        return True

    def _quarantine(self, u: Utterance, why: str) -> bool:
        self.quarantine.append((u, why))
        return False

    def report_for(self, candidate_id: str) -> list[EvidenceItem]:
        return list(self._by_candidate.get(candidate_id, ()))
