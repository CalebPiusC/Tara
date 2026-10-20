"""A live interview session with evidence-linked grading.

Rules enforced in code (not by prompt):
- every score must cite a quote that appears verbatim in a recent NON-Tara turn of the transcript;
- the criterion must exist in the rubric and the score must be 0 to 4;
- if candidate speaker ids are configured, the quote must come from one of them;
- results never contain a hire/reject recommendation and always say a person must decide;
- a criterion without evidence is reported as "insufficient evidence", never as a low or average score.
"""
from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .planner import SCALE, InterviewPlan

MAX_QUOTE = 400
MIN_QUOTE_CHARS = 12


class InterviewError(ValueError):
    pass


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.lower()).strip()


@dataclass(frozen=True)
class Assessment:
    qid: str
    criterion: str
    score: int
    rationale: str
    quote: str
    speaker: str
    attribution: str            # verified | unverified (speaker not tied to a configured candidate id)


@dataclass
class InterviewResult:
    interview_id: str
    candidate_id: str
    role_title: str
    started: str
    finished: str
    questions_total: int
    questions_covered: int
    criteria: list[dict[str, Any]]
    assessments: list[dict[str, Any]]
    flags: list[str]
    human_review_required: bool = True
    statement: str = ("This is AI-assisted decision support based on the quoted evidence. It is not a hiring decision: "
                      "a person must review the evidence and decide.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Transcript = Callable[[], Sequence[tuple[str, str]]]


class InterviewSession:
    def __init__(self, plan: InterviewPlan, candidate_id: str, transcript: Transcript, *,
                 candidate_speakers: frozenset[str] = frozenset(), interview_id: str = "", clock: Callable[[], float] = time.time):
        if not plan.questions:
            raise InterviewError("the plan has no questions")
        self.plan, self.candidate_id = plan, candidate_id
        self.interview_id = interview_id or f"{candidate_id}-{secrets.token_hex(3)}"
        self._transcript, self.candidate_speakers, self._clock = transcript, candidate_speakers, clock
        self.started = self._stamp()
        self.finished = ""
        self.index = 0
        self.asked: list[str] = []
        self.assessments: list[Assessment] = []
        self.notes: list[str] = []
        self.done = False

    def _stamp(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(self._clock()))

    # ---- flow ---------------------------------------------------------------------------------------------
    def next_question(self) -> dict[str, Any]:
        if self.done:
            raise InterviewError("the interview is finished")
        if self.index >= len(self.plan.questions):
            return {"done": True, "note": "all planned questions have been asked; wrap up and finish the interview"}
        q = self.plan.questions[self.index]
        self.index += 1
        self.asked.append(q.qid)
        out: dict[str, Any] = {"done": False, "qid": q.qid, "question": q.text, "criterion": q.criterion,
                               "number": self.index, "of": len(self.plan.questions)}
        if q.probes:
            out["follow_up_ideas"] = list(q.probes)
        if q.signals:
            out["good_answers_mention"] = list(q.signals)      # for judging only; never read out as the answer
        return out

    def record(self, qid: str, criterion: str, score: int, rationale: str, quote: str) -> dict[str, Any]:
        if self.done:
            raise InterviewError("the interview is finished")
        if qid not in self.asked:
            raise InterviewError("that question has not been asked yet")
        names = {c.name for c in self.plan.criteria}
        if criterion not in names:
            raise InterviewError(f"unknown criterion; use one of {sorted(names)}")
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 4:
            raise InterviewError("score must be a whole number from 0 to 4")
        q = quote.strip()
        if len(q) < MIN_QUOTE_CHARS:
            raise InterviewError("quote the candidate's own words (at least a short phrase) as evidence")
        speaker = self._verify_quote(q[:MAX_QUOTE])
        attribution = "verified" if (self.candidate_speakers and speaker in self.candidate_speakers) else "unverified"
        a = Assessment(qid, criterion, score, rationale.strip()[:300], q[:MAX_QUOTE], speaker, attribution)
        self.assessments.append(a)
        return {"status": "recorded", "criterion": criterion, "score": score, "label": SCALE[score], "attribution": attribution}

    def _verify_quote(self, quote: str) -> str:
        needle = _norm(quote)
        for speaker, text in reversed(list(self._transcript())):
            if speaker == "Tara":
                continue
            if needle and needle in _norm(text):
                if self.candidate_speakers and speaker not in self.candidate_speakers:
                    raise InterviewError("that quote was said by someone who is not the candidate")
                return speaker
        raise InterviewError("that quote does not appear in the recent conversation; quote the candidate exactly")

    def finish(self) -> InterviewResult:
        self.done = True
        self.finished = self._stamp()
        by: dict[str, list[Assessment]] = {}
        for a in self.assessments:
            by.setdefault(a.criterion, []).append(a)
        crit_rows: list[dict[str, Any]] = []
        flags: list[str] = []
        for c in self.plan.criteria:
            ev = by.get(c.name, [])
            if not ev:
                crit_rows.append({"criterion": c.name, "description": c.description, "evidence_count": 0, "average": None,
                                  "label": "insufficient evidence"})
                flags.append(f"insufficient evidence for {c.name}")
                continue
            avg = sum(a.score for a in ev) / len(ev)
            crit_rows.append({"criterion": c.name, "description": c.description, "evidence_count": len(ev),
                              "average": round(avg, 2), "label": SCALE[min(4, max(0, round(avg)))]})
        if any(a.attribution == "unverified" for a in self.assessments):
            flags.append("some quotes could not be tied to a confirmed candidate speaker; check them against the recording")
        covered = len({a.qid for a in self.assessments})
        if covered < len(self.asked):
            flags.append(f"{len(self.asked) - covered} asked question(s) have no recorded assessment")
        if len(self.asked) < len(self.plan.questions):
            flags.append(f"only {len(self.asked)} of {len(self.plan.questions)} planned questions were asked")
        return InterviewResult(
            self.interview_id, self.candidate_id, self.plan.role_title, self.started, self.finished, len(self.plan.questions),
            covered, crit_rows, [asdict(a) for a in self.assessments], flags)


def result_facts(result: InterviewResult, plan: InterviewPlan, brief: str = "") -> dict[str, Any]:
    """Facts for the interview report document: only recorded evidence, nothing inferred."""
    qtext = {q.qid: q.text for q in plan.questions}
    return {
        "Interview details": f"Role: {result.role_title}. Candidate reference: {result.candidate_id}. Started: {result.started}. "
                             f"Finished: {result.finished}. Questions covered: {result.questions_covered} of {result.questions_total}.",
        "Candidate brief (from the CV, portfolio and GitHub; unverified claims)": brief or "Not recorded",
        "Criteria results (score 0-4, average of recorded evidence)": [
            f"{c['criterion']}: {c['average'] if c['average'] is not None else 'insufficient evidence'}"
            f" ({c['evidence_count']} evidence item(s); {c['label']})" for c in result.criteria],
        "Evidence (question, criterion, score, quote, rationale)": [
            f"[{qtext.get(a['qid'], a['qid'])}] {a['criterion']} = {a['score']}; quote: \"{a['quote']}\"; "
            f"rationale: {a['rationale'] or 'none'}; attribution: {a['attribution']}" for a in result.assessments] or ["None recorded"],
        "Flags for the reviewer": result.flags or ["None"],
        "Statement": result.statement,
    }
