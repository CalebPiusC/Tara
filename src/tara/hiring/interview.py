"""A live interview session with semantic grading, attempts and a code-owned deadline.

Rules enforced in code (not by prompt):
- the new path evaluates the meaning of an answer through `hiring.evaluator`; the reference signals are guidance,
  not an answer key, and valid alternatives can earn credit;
- an incorrect or unclear first answer gets at most one retry; every attempt is preserved and only the final attempt
  feeds the criterion averages;
- each question has a monotonic deadline; a timeout records zero only when no answer was completed, and a late
  evaluation cannot reopen or advance a question twice;
- the legacy path still requires a quote that appears verbatim in a recent non-Tara turn, and scores it 0 to 4;
- results never contain a hire/reject recommendation and always say a person must decide;
- a criterion without evidence is reported as "insufficient evidence", never as a low or average score.
"""
from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .evaluator import AttemptAssessment, Evaluation
from .planner import SCALE, InterviewPlan, Question

MAX_QUOTE = 400
MIN_QUOTE_CHARS = 12
DEFAULT_QUESTION_LIMIT_S = 180.0
DEFAULT_RETRY_LIMIT = 1
MIN_RETRY_SECONDS = 5.0


class InterviewError(ValueError):
    pass


class InterviewExpired(InterviewError):
    """The question deadline passed before an attempt could start; carries the timeout decision."""

    def __init__(self, message: str, decision: dict[str, Any]):
        super().__init__(message)
        self.decision = decision


class InterviewState(str, Enum):
    """Explicit interview states. Transitions are checked in code, not by the prompt."""

    WAITING_FOR_CANDIDATE = "waiting_for_candidate"
    WELCOMING = "welcoming"
    ASKING_QUESTION = "asking_question"
    WAITING_FOR_ANSWER = "waiting_for_answer"
    EVALUATING_ANSWER = "evaluating_answer"
    RETRYING_QUESTION = "retrying_question"
    PROVIDING_FEEDBACK = "providing_feedback"
    ADVANCING_QUESTION = "advancing_question"
    INTERVIEW_COMPLETED = "interview_completed"


VALID_TRANSITIONS: dict[InterviewState, frozenset[InterviewState]] = {
    InterviewState.WAITING_FOR_CANDIDATE: frozenset({InterviewState.WELCOMING, InterviewState.ASKING_QUESTION,
                                                      InterviewState.INTERVIEW_COMPLETED}),
    InterviewState.WELCOMING: frozenset({InterviewState.ASKING_QUESTION, InterviewState.WAITING_FOR_CANDIDATE,
                                         InterviewState.INTERVIEW_COMPLETED}),
    InterviewState.ASKING_QUESTION: frozenset({InterviewState.WAITING_FOR_ANSWER, InterviewState.ADVANCING_QUESTION,
                                                InterviewState.INTERVIEW_COMPLETED}),
    InterviewState.WAITING_FOR_ANSWER: frozenset({InterviewState.EVALUATING_ANSWER, InterviewState.ADVANCING_QUESTION,
                                                   InterviewState.PROVIDING_FEEDBACK, InterviewState.INTERVIEW_COMPLETED}),
    InterviewState.EVALUATING_ANSWER: frozenset({InterviewState.WAITING_FOR_ANSWER, InterviewState.RETRYING_QUESTION,
                                                  InterviewState.PROVIDING_FEEDBACK, InterviewState.ADVANCING_QUESTION,
                                                  InterviewState.INTERVIEW_COMPLETED}),
    InterviewState.RETRYING_QUESTION: frozenset({InterviewState.EVALUATING_ANSWER, InterviewState.WAITING_FOR_ANSWER,
                                                  InterviewState.ADVANCING_QUESTION, InterviewState.INTERVIEW_COMPLETED}),
    InterviewState.PROVIDING_FEEDBACK: frozenset({InterviewState.ADVANCING_QUESTION, InterviewState.WAITING_FOR_ANSWER,
                                                   InterviewState.INTERVIEW_COMPLETED}),
    InterviewState.ADVANCING_QUESTION: frozenset({InterviewState.ASKING_QUESTION, InterviewState.WAITING_FOR_ANSWER,
                                                   InterviewState.INTERVIEW_COMPLETED}),
    InterviewState.INTERVIEW_COMPLETED: frozenset(),
}


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
    timed_out: bool = False
    attempt: int = 1


@dataclass
class QuestionProgress:
    """Everything about one question: every attempt, the final score, and its deadline."""

    qid: str
    criterion: str
    text: str
    attempts: list[AttemptAssessment] = field(default_factory=list)
    final_score_10: float | None = None
    final_score_4: int | None = None
    timed_out: bool = False
    skipped: bool = False
    started_at: float = 0.0
    deadline: float = 0.0
    clock_started: bool = False       # the fallback clock starts at issue time; playback completion may start it once

    @property
    def finalized(self) -> bool:
        return self.final_score_10 is not None or self.skipped

    def to_dict(self) -> dict[str, Any]:
        return {"qid": self.qid, "criterion": self.criterion, "question": self.text,
                "attempts": [asdict(a) for a in self.attempts], "final_score_10": self.final_score_10,
                "final_score_4": self.final_score_4, "timed_out": self.timed_out, "skipped": self.skipped,
                "started_at": self.started_at, "deadline": self.deadline, "clock_started": self.clock_started}


@dataclass(frozen=True)
class AttemptContext:
    attempt_id: str
    qid: str
    attempt: int
    answer: str
    speaker: str
    received_at: float


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
    question_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Transcript = Callable[[], Sequence[tuple[str, str]]]


class InterviewSession:
    def __init__(self, plan: InterviewPlan, candidate_id: str, transcript: Transcript, *,
                 candidate_speakers: frozenset[str] = frozenset(), interview_id: str = "", clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic, question_limit_s: float = DEFAULT_QUESTION_LIMIT_S,
                 retry_limit: int = DEFAULT_RETRY_LIMIT):
        if not plan.questions:
            raise InterviewError("the plan has no questions")
        if question_limit_s <= 0:
            raise InterviewError("the question time limit must be positive")
        self.plan, self.candidate_id = plan, candidate_id
        self.interview_id = interview_id or f"{candidate_id}-{secrets.token_hex(3)}"
        self._transcript, self.candidate_speakers, self._clock = transcript, candidate_speakers, clock
        self._monotonic = monotonic
        self.question_limit_s = float(question_limit_s)
        self.retry_limit = max(0, min(2, int(retry_limit)))
        self.started = self._stamp()
        self.finished = ""
        self.index = 0
        self.asked: list[str] = []
        self.assessments: list[Assessment] = []
        self.question_results: list[QuestionProgress] = []
        self.notes: list[str] = []
        self.done = False
        self.state = InterviewState.WAITING_FOR_CANDIDATE
        self.welcomed = False
        self._question: QuestionProgress | None = None
        self._inflight: AttemptContext | None = None
        self._attempt_seq = 0
        self._last_timeout: dict[str, Any] | None = None

    def _stamp(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(self._clock()))

    def _transition(self, state: InterviewState) -> None:
        if state is self.state:
            return
        if state not in VALID_TRANSITIONS[self.state]:
            raise InterviewError(f"invalid interview transition {self.state.value} -> {state.value}")
        self.state = state

    # ---- flow ---------------------------------------------------------------------------------------------
    def mark_welcomed(self) -> None:
        """The welcome has been delivered. Kept separate so reconnect/duplicate starts cannot repeat it."""
        if self.done or self.welcomed:
            return
        self.welcomed = True
        if self.state is InterviewState.WAITING_FOR_CANDIDATE:
            self._transition(InterviewState.WELCOMING)

    def _payload(self, q: Question, *, duplicate: bool = False, evaluating: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"done": False, "qid": q.qid, "question": q.text, "criterion": q.criterion,
                               "number": self.asked.index(q.qid) + 1, "of": len(self.plan.questions),
                               "retry_limit": self.retry_limit, "time_limit_s": self.question_limit_s}
        progress = self._question
        if progress is not None and progress.qid == q.qid:
            out["attempt"] = len(progress.attempts) + 1
            out["retry_count"] = len(progress.attempts)
            out["time_remaining_s"] = round(max(0.0, progress.deadline - self._monotonic()), 1)
        if q.probes:
            out["follow_up_ideas"] = list(q.probes)
        if q.signals:
            out["good_answers_mention"] = list(q.signals)      # guidance only; never read out as the answer
        if duplicate:
            out["duplicate"] = True
            out["note"] = "This question is still open; do not ask it again. Wait for the candidate's answer."
        if evaluating:
            out["evaluating"] = True
            out["note"] = "An answer is already being evaluated. Do not ask this question again or start another evaluation."
        return out

    def next_question(self, *, force: bool = False) -> dict[str, Any]:
        """Return the next question. Repeating the call while a question is open is idempotent.

        `force=True` is an explicit skip for operators/tests; the model-facing tool never uses it.  A retry never
        advances the index because the open question is returned instead.
        """
        if self.done:
            raise InterviewError("the interview is finished")
        current = self._question
        if current is not None and not current.finalized:
            if force:
                self.skip_current()
            elif self.state is InterviewState.EVALUATING_ANSWER:
                q = next(q for q in self.plan.questions if q.qid == current.qid)
                return self._payload(q, duplicate=True, evaluating=True)
            else:
                q = next(q for q in self.plan.questions if q.qid == current.qid)
                return self._payload(q, duplicate=True)
        if self.index >= len(self.plan.questions):
            return {"done": True, "note": "all planned questions have been asked; wrap up and finish the interview"}
        q = self.plan.questions[self.index]
        self.index += 1
        self.asked.append(q.qid)
        now = self._monotonic()
        self._question = QuestionProgress(q.qid, q.criterion, q.text, started_at=now,
                                          deadline=now + self.question_limit_s)
        self.question_results.append(self._question)
        if self.state not in (InterviewState.ASKING_QUESTION, InterviewState.ADVANCING_QUESTION,
                              InterviewState.WELCOMING, InterviewState.WAITING_FOR_CANDIDATE):
            raise InterviewError(f"cannot ask a new question while {self.state.value}")
        self._transition(InterviewState.ASKING_QUESTION)
        self._transition(InterviewState.WAITING_FOR_ANSWER)
        return self._payload(q)

    def start_question_clock(self, qid: str, *, now: float | None = None) -> bool:
        """Start the answer clock when Tara has finished speaking the question.

        `next_question` sets a fallback deadline at issue time so a muted or failed voice path still times out. This
        method moves that deadline once, to the first completed question playback, and is deliberately idempotent:
        duplicate playback callbacks, a retry prompt, a late callback for an earlier question and an answer that has
        already started can never reset it.
        """
        if self.done:
            return False
        progress = self._question
        if progress is None or progress.qid != qid or progress.finalized or progress.clock_started:
            return False
        if progress.attempts or self.state is not InterviewState.WAITING_FOR_ANSWER:
            return False
        now = self._monotonic() if now is None else float(now)
        if now >= progress.deadline:
            return False                 # the fallback deadline already elapsed; do not rescue a timed-out question
        progress.clock_started = True
        new_deadline = now + self.question_limit_s
        if new_deadline <= progress.deadline:
            return False                 # never shorten the fallback deadline
        progress.started_at = now
        progress.deadline = new_deadline
        return True

    def record(self, qid: str, criterion: str, score: int, rationale: str, quote: str) -> dict[str, Any]:
        """Legacy model-supplied scoring path. Kept for compatibility; new interviews use semantic evaluation below."""
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
        progress = self._question
        if progress is not None and progress.qid == qid:
            if not progress.finalized:
                self._transition(InterviewState.PROVIDING_FEEDBACK)
                self._transition(InterviewState.ADVANCING_QUESTION)
            progress.final_score_4 = score
            progress.final_score_10 = round(score * 2.5, 1)
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

    # ---- semantic evaluation, retries and the deadline -----------------------------------------------------
    def current_question(self) -> QuestionProgress | None:
        return self._question

    def begin_attempt(self, qid: str, answer: str, *, speaker: str = "", now: float | None = None) -> AttemptContext:
        """Start one evaluation. The answer must be non-empty and different from every previous attempt."""
        if self.done:
            raise InterviewError("the interview is finished")
        progress = self._question
        if progress is None or progress.qid != qid:
            raise InterviewError("that is not the current question")
        if progress.finalized:
            if progress.timed_out:
                raise InterviewExpired("the three-minute limit for this question has been reached",
                                       self._last_timeout or {"status": "timed_out", "qid": qid,
                                                              "should_advance": True})
            raise InterviewError("that question has already been evaluated")
        if self.state is InterviewState.EVALUATING_ANSWER:
            raise InterviewError("an evaluation is already in progress")
        if self.state not in (InterviewState.WAITING_FOR_ANSWER, InterviewState.RETRYING_QUESTION):
            raise InterviewError(f"cannot evaluate an answer while {self.state.value}")
        text = answer.strip()
        if not text:
            raise InterviewError("I have not heard an answer yet")
        norm = _norm(text)
        if any(_norm(a.answer) == norm for a in progress.attempts):
            raise InterviewError("that is the same answer as the previous attempt; wait for the candidate's retry")
        attempt_no = len(progress.attempts) + 1
        if attempt_no > self.retry_limit + 1:
            raise InterviewError("the retry limit for this question has been reached")
        now = self._monotonic() if now is None else now
        if now >= progress.deadline:
            decision = self.expire_current(qid=qid, now=now)
            raise InterviewExpired("the three-minute limit for this question has been reached",
                                   decision or {"status": "timed_out", "qid": qid, "should_advance": True})
        self._attempt_seq += 1
        ctx = AttemptContext(f"{qid}:{attempt_no}:{self._attempt_seq}", qid, attempt_no, text, speaker, now)
        self._inflight = ctx
        self._transition(InterviewState.EVALUATING_ANSWER)
        return ctx

    def cancel_attempt(self, attempt_id: str) -> bool:
        """Evaluation failed without a result: reopen the same question without spending the attempt."""
        if self._inflight is None or self._inflight.attempt_id != attempt_id:
            return False
        self._inflight = None
        if self._question is not None and self._question.attempts:
            self._transition(InterviewState.RETRYING_QUESTION)
        else:
            self._transition(InterviewState.WAITING_FOR_ANSWER)
        return True

    def finish_attempt(self, attempt_id: str, evaluation: Evaluation) -> dict[str, Any]:
        """Apply the retry policy and either finalize the question or ask for one retry."""
        if self.done or self._inflight is None or self._inflight.attempt_id != attempt_id:
            return {"status": "ignored", "reason": "stale_attempt", "should_advance": False}
        progress = self._question
        ctx = self._inflight
        if progress is None or progress.qid != ctx.qid or progress.finalized:
            self._inflight = None
            return {"status": "ignored", "reason": "question_changed", "should_advance": False}
        now = self._monotonic()
        remaining = progress.deadline - now
        can_retry = (ctx.attempt <= self.retry_limit and evaluation.retry_recommended and remaining > MIN_RETRY_SECONDS)
        retry_count = ctx.attempt - 1
        attempt = AttemptAssessment(
            attempt=ctx.attempt, retry_count=retry_count, answer=ctx.answer,
            correctness=evaluation.correctness, technical_accuracy=evaluation.technical_accuracy,
            completeness=evaluation.completeness, reasoning=evaluation.reasoning, score=evaluation.score,
            feedback=evaluation.feedback, hint=evaluation.hint, needs_retry=can_retry,
            should_advance=not can_retry, missing_concepts=evaluation.missing_concepts, source=evaluation.source)
        progress.attempts.append(attempt)
        self._inflight = None
        if can_retry:
            self._transition(InterviewState.RETRYING_QUESTION)
            return self._decision(progress, attempt, "retry", timed_out=False)
        self._transition(InterviewState.PROVIDING_FEEDBACK)
        self._finalize_progress(progress, attempt, ctx.speaker, timed_out=False)
        return self._decision(progress, attempt, "finish" if self.index >= len(self.plan.questions) else "next_question",
                              timed_out=False)

    def expire_current(self, *, qid: str | None = None, now: float | None = None) -> dict[str, Any] | None:
        """Enforce the deadline. Idempotent, and it invalidates any evaluation that started after the deadline."""
        if self.done:
            return None
        progress = self._question
        if progress is None or progress.finalized:
            return None
        if qid is not None and progress.qid != qid:
            return None
        now = self._monotonic() if now is None else now
        if now < progress.deadline:
            return None
        if (self.state is InterviewState.EVALUATING_ANSWER and self._inflight is not None
                and self._inflight.received_at <= progress.deadline):
            return None                 # the complete answer arrived in time; let the evaluation finish
        progress.timed_out = True
        self._inflight = None
        last = progress.attempts[-1] if progress.attempts else None
        if last is not None:
            last = AttemptAssessment(**{**asdict(last), "timed_out": True, "needs_retry": False, "should_advance": True})
            progress.attempts[-1] = last
            self._finalize_progress(progress, last, "", timed_out=True)
        else:
            self._finalize_progress(progress, None, "", timed_out=True)
        self._transition(InterviewState.ADVANCING_QUESTION)
        decision = {"status": "timed_out", "qid": progress.qid, "criterion": progress.criterion,
                    "should_advance": True,
                    "next_action": "finish" if self.index >= len(self.plan.questions) else "next_question",
                    "say": "Say briefly that the time for that question is up, do not evaluate the incomplete answer, "
                           "and move on. Do not reveal an expected answer."}
        self._last_timeout = decision
        return decision

    def skip_current(self) -> None:
        """Operator/test-only explicit skip. The model cannot call this through the interview tools."""
        progress = self._question
        if progress is None or progress.finalized:
            return
        progress.skipped = True
        progress.final_score_10 = None
        progress.final_score_4 = None
        self._inflight = None
        self._transition(InterviewState.ADVANCING_QUESTION)

    def _finalize_progress(self, progress: QuestionProgress, attempt: AttemptAssessment | None, speaker: str, *,
                           timed_out: bool) -> None:
        if attempt is not None:
            progress.final_score_10 = attempt.score
            progress.final_score_4 = int(round(min(10.0, max(0.0, attempt.score)) / 2.5))
            attribution = "verified" if (self.candidate_speakers and speaker in self.candidate_speakers) else "unverified"
            rationale = attempt.feedback
            if timed_out:
                rationale = (rationale + " The retry was not completed within the three-minute limit.").strip()
            self.assessments.append(Assessment(
                progress.qid, progress.criterion, progress.final_score_4, rationale[:300],
                attempt.answer[:MAX_QUOTE], speaker, attribution, timed_out=timed_out, attempt=attempt.attempt))
        else:
            progress.final_score_10 = 0.0
            progress.final_score_4 = 0
            self.assessments.append(Assessment(
                progress.qid, progress.criterion, 0, "No complete answer within the three-minute limit.",
                "", "", "unverified", timed_out=True, attempt=0))
        progress.timed_out = timed_out
        if self.state is not InterviewState.ADVANCING_QUESTION:
            self._transition(InterviewState.ADVANCING_QUESTION)

    def _decision(self, progress: QuestionProgress, attempt: AttemptAssessment, next_action: str, *, timed_out: bool) -> dict[str, Any]:
        next_step = ("finish the interview and thank the candidate" if next_action == "finish"
                     else "move to the next question")
        if attempt.needs_retry:
            say = (f"Give one short, proportional hint without revealing the expected answer: {attempt.hint or attempt.feedback} "
                   "Ask the candidate to try that once more.")
        elif attempt.correctness in ("correct", "valid_alternative", "mostly_correct"):
            say = f"Acknowledge this specific strength briefly: {attempt.feedback} Then {next_step}."
        elif attempt.correctness == "partially_correct":
            say = (f"Acknowledge what was valid and mention the gap briefly: {attempt.feedback} "
                   f"Do not pretend the answer was complete. Then {next_step}.")
        else:
            say = (f"Briefly and respectfully acknowledge the attempt, say you will {next_step}, and do not reveal the expected "
                   "answer or turn the interview into a tutorial.")
        return {"status": "timed_out" if timed_out else "evaluated", "qid": progress.qid,
                "attempt": attempt.attempt, "retry_count": attempt.retry_count, "needs_retry": attempt.needs_retry,
                "should_advance": attempt.should_advance, "next_action": next_action,
                "assessment": {"correctness": attempt.correctness, "technical_accuracy": attempt.technical_accuracy,
                               "completeness": attempt.completeness, "reasoning": attempt.reasoning,
                               "score": attempt.score, "feedback": attempt.feedback, "hint": attempt.hint,
                               "needs_retry": attempt.needs_retry, "retry_count": attempt.retry_count,
                               "should_advance": attempt.should_advance,
                               "missing_concepts": list(attempt.missing_concepts), "source": attempt.source,
                               "timed_out": timed_out},
                "final_score": progress.final_score_10, "time_remaining_s": round(max(0.0, progress.deadline - self._monotonic()), 1),
                "say": say}

    def finish(self) -> InterviewResult:
        self.done = True
        if self.state is not InterviewState.INTERVIEW_COMPLETED:
            self._transition(InterviewState.INTERVIEW_COMPLETED)
        self._inflight = None
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
        if any(a.timed_out for a in self.assessments):
            flags.append("one or more questions reached the three-minute limit")
        if any(p.skipped for p in self.question_results):
            flags.append("one or more questions were skipped without an evaluation")
        if any(len(p.attempts) > 1 for p in self.question_results):
            flags.append("one or more questions used a retry; the first attempt is preserved in question_results")
        covered = len({a.qid for a in self.assessments})
        if covered < len(self.asked):
            flags.append(f"{len(self.asked) - covered} asked question(s) have no recorded assessment")
        if len(self.asked) < len(self.plan.questions):
            flags.append(f"only {len(self.asked)} of {len(self.plan.questions)} planned questions were asked")
        return InterviewResult(
            self.interview_id, self.candidate_id, self.plan.role_title, self.started, self.finished, len(self.plan.questions),
            covered, crit_rows, [asdict(a) for a in self.assessments], flags,
            question_results=[p.to_dict() for p in self.question_results])


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
            (f"[{qtext.get(a['qid'], a['qid'])}] {a['criterion']} = {a['score']}; timed out after a completed attempt; "
             f"quote: \"{a['quote']}\"; rationale: {a['rationale'] or 'none'}; attribution: {a['attribution']}")
            if a.get("timed_out") and a.get("quote") else
            (f"[{qtext.get(a['qid'], a['qid'])}] {a['criterion']} = {a['score']}; timed out, no usable answer; "
             f"rationale: {a['rationale'] or 'none'}")
            if a.get("timed_out") else
            (f"[{qtext.get(a['qid'], a['qid'])}] {a['criterion']} = {a['score']}; quote: \"{a['quote']}\"; "
             f"rationale: {a['rationale'] or 'none'}; attribution: {a['attribution']}")
            for a in result.assessments] or ["None recorded"],
        "Per-question outcomes (every attempt is preserved; final score is the last completed attempt)": [
            f"{q['qid']}: final {q['final_score_10']}/10 (report scale {q['final_score_4']}/4)"
            + (" [timed out]" if q.get("timed_out") else "")
            + (" [skipped]" if q.get("skipped") else "")
            + "".join(f"; attempt {a['attempt']}: {a['score']}/10, {a['correctness']}, "
                      f"missing: {', '.join(a.get('missing_concepts') or []) or 'none'}" for a in q.get("attempts", []))
            for q in result.question_results] or ["None recorded"],
        "Flags for the reviewer": result.flags or ["None"],
        "Statement": result.statement,
    }
