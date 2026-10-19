"""Participant registry and speaker attribution (docs/PARTICIPANT_IDENTITY.md).

Rules enforced here:
- A diarization label is never a person; it maps to one only via an explicit,
  versioned, confidence-bearing LabelMap entry that is wiped on re-cluster.
- Short utterances without a per-participant stream are unattributed.
- Ambiguous top candidates yield participant_id None.
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import HintKind, SpeakerAttribution, SpeakerHint


@dataclass
class Participant:
    participant_id: str
    display_name: str
    present: bool = True


class ParticipantRegistry:
    def __init__(self) -> None:
        self._p: dict[str, Participant] = {}

    def join(self, pid: str, name: str) -> Participant:
        p = self._p.get(pid)
        if p:
            p.present, p.display_name = True, name
            return p
        p = self._p[pid] = Participant(pid, name)
        return p

    def leave(self, pid: str) -> None:
        if pid in self._p:
            self._p[pid].present = False

    def rename(self, pid: str, name: str) -> None:
        """Rename keeps the participant id: identity follows the platform id, not the name."""
        if pid not in self._p:
            raise KeyError(pid)
        self._p[pid].display_name = name

    def get(self, pid: str) -> Participant | None:
        return self._p.get(pid)

    def present_ids(self) -> list[str]:
        return [p.participant_id for p in self._p.values() if p.present]

    def find_by_name(self, name: str) -> list[str]:
        n = name.strip().lower()
        return [p.participant_id for p in self._p.values() if p.display_name.strip().lower() == n]


class LabelMap:
    """Diarization label -> participant, versioned. Re-clustering invalidates all entries."""

    def __init__(self) -> None:
        self.version = 0
        self._m: dict[str, tuple[str, float]] = {}

    def set(self, label: str, pid: str, confidence: float) -> None:
        if not 0.0 < confidence <= 1.0:
            raise ValueError("confidence must be in (0, 1]")
        self._m[label] = (pid, confidence)
        self.version += 1

    def lookup(self, label: str) -> tuple[str, float] | None:
        return self._m.get(label)

    def invalidate_all(self) -> None:
        """Call when the diarizer re-clusters: old labels no longer mean anything."""
        self._m.clear()
        self.version += 1

    def correct(self, label: str, pid: str) -> None:
        """Human correction: strong mapping, new version."""
        self.set(label, pid, 0.99)


@dataclass(frozen=True)
class AttributorParams:
    min_duration_ms: int = 800       # shorter, with no stream hint: unattributed
    min_margin: float = 0.15         # top must beat runner-up by this much
    stream_weight: float = 0.99
    active_speaker_cap: float = 0.6  # platform indicator alone never exceeds this
    lag_penalty_per_s: float = 0.15  # active-speaker hints decay with lag
    diarization_cap: float = 0.85


class SpeakerAttributor:
    def __init__(self, registry: ParticipantRegistry, labels: LabelMap, params: AttributorParams | None = None):
        self.registry, self.labels = registry, labels
        self.p = params or AttributorParams()

    def attribute(self, start_ms: int, end_ms: int, hints: list[SpeakerHint], overlap: bool = False) -> SpeakerAttribution:
        p = self.p
        scores: dict[str, float] = {}
        evidence: list[str] = []
        has_stream = False

        def bump(pid: str, s: float) -> None:
            # noisy-or combination: independent supporting evidence raises confidence
            scores[pid] = 1 - (1 - scores.get(pid, 0.0)) * (1 - s)

        for h in hints:
            if h.kind is HintKind.STREAM and h.participant_id:
                has_stream = True
                bump(h.participant_id, min(h.score, p.stream_weight))
                evidence.append(f"stream:{h.participant_id}")
            elif h.kind is HintKind.ACTIVE_SPEAKER and h.participant_id:
                lag_s = abs(h.ts_ms - start_ms) / 1000
                s = max(0.0, min(h.score, p.active_speaker_cap) - p.lag_penalty_per_s * lag_s)
                if s > 0:
                    bump(h.participant_id, s)
                    evidence.append(f"active_speaker:{h.participant_id}")
            elif h.kind is HintKind.DIARIZATION and h.label:
                m = self.labels.lookup(h.label)
                evidence.append(f"diarization:{h.label}")
                if m:
                    bump(m[0], min(m[1], p.diarization_cap) * h.score)
            elif h.kind is HintKind.SELF_INTRO and h.participant_id:
                bump(h.participant_id, min(h.score, 0.95))
                evidence.append(f"self_intro:{h.participant_id}")

        # only participants currently present can be the speaker
        present = set(self.registry.present_ids())
        scores = {k: v for k, v in scores.items() if k in present}
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        cands = tuple(ranked[:3])
        ver = self.labels.version

        if (end_ms - start_ms) < p.min_duration_ms and not has_stream:
            return SpeakerAttribution(None, ranked[0][1] if ranked else 0.0, overlap,
                                      tuple(evidence + ["too_short"]), ver, cands)
        if not ranked:
            return SpeakerAttribution(None, 0.0, overlap, tuple(evidence), ver, cands)
        top, second = ranked[0], (ranked[1] if len(ranked) > 1 else (None, 0.0))
        if top[1] - second[1] < p.min_margin:
            return SpeakerAttribution(None, top[1], overlap, tuple(evidence + ["ambiguous"]), ver, cands)
        return SpeakerAttribution(top[0], top[1], overlap, tuple(evidence), ver, cands)
