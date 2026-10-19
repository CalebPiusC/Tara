"""Report generation from stored evidence only. Reports are decision support:
every interview report says human review is required and lists excluded
(quarantined) utterances so gaps are visible, never hidden."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .interview import InterviewEngine


@dataclass
class MeetingLog:
    decisions: list[dict[str, str]] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    objections: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    approvals_needed: list[str] = field(default_factory=list)


def interview_report(engine: InterviewEngine, names: dict[str, str]) -> dict[str, Any]:
    ev = engine.evidence
    out: dict[str, Any] = {"human_review_required": True, "candidates": {}, "excluded_utterances": [
        {"event_id": u.event_id, "reason": why} for u, why in ev.quarantine]}
    for cid in engine.candidates:
        items = ev.report_for(cid)
        ass = [a for a in engine.assessments if a.candidate_id == cid]
        out["candidates"][cid] = {
            "name": names.get(cid, cid),
            "evidence": [{"event_id": e.event_id, "quote": e.quote} for e in items],
            "assessments": [{"criterion": a.criterion, "kind": a.kind, "note": a.note,
                             "evidence": list(a.evidence_event_ids), "score": a.score} for a in ass],
            "insufficient_evidence": len(items) == 0,
        }
    if engine.unsolicited:
        out["unsolicited_contributions"] = [u.event_id for u in engine.unsolicited]
    return out


def meeting_notes(log: MeetingLog) -> dict[str, Any]:
    return {"decisions": log.decisions, "actions": log.actions, "open_questions": log.open_questions}


def pitch_report(log: MeetingLog) -> dict[str, Any]:
    return {"requirements": log.requirements, "objections": log.objections,
            "unanswered_questions": log.unanswered, "actions_requiring_human_approval": log.approvals_needed}
