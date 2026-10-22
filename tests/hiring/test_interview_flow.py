"""Semantic interview evaluation, attempts, retries, the three-minute deadline and the tool-level flow.

All model calls are fake; the clock is injected. No live API traffic is used here.
"""
import asyncio
import dataclasses

import pytest

from tara.core.errors import MalformedOutput
from tara.core.settings import RuntimeSettings, validate
from tara.floor.participation import Attribution
from tara.hiring.evaluator import AnswerEvaluator, Evaluation, parse_evaluation
from tara.hiring.interview import (
    InterviewError,
    InterviewExpired,
    InterviewSession,
    InterviewState,
)
from tara.hiring.planner import InterviewPlan, Question, default_criteria
from tara.testing.fakes import FakeLLMProvider
from tara.voice.turns import TurnRecord
from tests.work.test_work_integration import acall, aio, prepare, prepared

run = asyncio.run


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def interview_plan() -> InterviewPlan:
    return InterviewPlan(
        "Go Engineer",
        default_criteria(["Go"]),
        [Question("q1", "How would you prevent duplicate payments?", "Go", "core", ("What storage?",),
                  ("idempotency", "unique constraint")),
         Question("q2", "Tell me about a failure you fixed.", "Problem solving")],
    )


def session(*, clock: Clock | None = None, limit: float = 180.0) -> InterviewSession:
    return InterviewSession(interview_plan(), "alex", lambda: (),
                            candidate_speakers=frozenset({"Alex"}), interview_id="alex-1",
                            monotonic=clock or Clock(), question_limit_s=limit, retry_limit=1)


def evaluation(correctness: str, score: float, *, technical: int = 8, completeness: int = 7,
               reasoning: int = 7, feedback: str = "Specific feedback.", hint: str = "Think about failure cases.",
               missing: tuple[str, ...] = ()) -> Evaluation:
    return Evaluation(correctness, technical, completeness, reasoning, score, feedback, hint, missing)


# ---- rubric -------------------------------------------------------------------------------------------------------------------------
def test_rubric_computes_the_score_and_caps_incorrect_and_unclear_answers():
    wrong = parse_evaluation({"correctness": "incorrect", "technical_accuracy": 9, "completeness": 9,
                              "reasoning": 9, "feedback": "Not the right mechanism."})
    assert wrong.score == 3.0                       # confidence and length do not rescue a wrong answer
    unclear = parse_evaluation({"correctness": "needs_clarification", "technical_accuracy": 9,
                                "completeness": 9, "reasoning": 9, "feedback": "Too vague."})
    assert unclear.score == 3.0
    alternative = parse_evaluation({"correctness": "valid_alternative", "technical_accuracy": 8,
                                    "completeness": 8, "reasoning": 8, "feedback": "A valid different design."})
    assert alternative.score == 8.0 and alternative.pass_mark
    incomplete = parse_evaluation({"correctness": "correct", "technical_accuracy": 9, "completeness": 4,
                                   "reasoning": 8, "feedback": "Accurate but missing a requirement."})
    assert incomplete.correctness == "partially_correct" and not incomplete.pass_mark
    mostly = parse_evaluation({"correctness": "mostly_correct", "technical_accuracy": 9, "completeness": 4,
                               "reasoning": 8, "feedback": "Close, but a requirement is missing."})
    assert mostly.correctness == "partially_correct" and not mostly.pass_mark
    for bad in ({"correctness": "correct", "technical_accuracy": 11, "completeness": 5, "reasoning": 5,
                 "feedback": "x"}, {"correctness": "made_up", "technical_accuracy": 5, "completeness": 5,
                                    "reasoning": 5, "feedback": "x"}):
        with pytest.raises(MalformedOutput):
            parse_evaluation(bad)


def test_fallback_is_deterministic_and_conservative_when_the_model_is_unavailable():
    async def go():
        evaluator = AnswerEvaluator(None)
        q = interview_plan().questions[0]
        good = await evaluator.evaluate(q, "I would put a unique constraint on the idempotency key because it makes retries safe.")
        partial = await evaluator.evaluate(q, "I would put a unique constraint on the record.")
        incomplete = await evaluator.evaluate(q, "I would add an idempotency key.")
        generic = await evaluator.evaluate(q, "I would use a unique identifier because it is simpler.")
        bad = await evaluator.evaluate(q, "Use a distributed lock and a saga, because it is safer.")
        vague = await evaluator.evaluate(q, "It depends.")
        return good, partial, incomplete, generic, bad, vague
    good, partial, incomplete, generic, bad, vague = run(go())
    assert good.source == "fallback" and good.pass_mark and good.correctness == "correct"
    # One missing concept is not a pass, even when the answer sounds confident.
    assert partial.source == "fallback" and partial.correctness == "partially_correct" and not partial.pass_mark
    assert incomplete.source == "fallback" and incomplete.correctness == "partially_correct" and not incomplete.pass_mark
    # A single generic word from a two-word concept is not evidence that the concept was covered.
    assert generic.source == "fallback" and generic.correctness == "incorrect" and not generic.pass_mark
    assert bad.source == "fallback" and bad.correctness == "incorrect" and not bad.pass_mark
    assert vague.source == "fallback" and vague.correctness == "needs_clarification" and vague.retry_recommended
    assert good.score > partial.score > generic.score


def test_model_evaluation_accepts_paraphrase_and_alternative_approaches():
    async def go():
        calls = []

        def responder(req, attempt):
            calls.append(req.user)
            return {"correctness": "valid_alternative", "technical_accuracy": 8, "completeness": 7,
                    "reasoning": 8, "feedback": "A different but valid approach.",
                    "hint": "", "missing_concepts": []}

        evaluator = AnswerEvaluator(FakeLLMProvider(responder), timeout_s=1)
        return await evaluator.evaluate(interview_plan().questions[0],
                                        "I would use a request key with a uniqueness rule, not the exact terms from the notes.")
    result = run(go())
    assert result.correctness == "valid_alternative" and result.pass_mark
    assert result.source == "model"


# ---- attempts, retries and state -----------------------------------------------------------------------------------------------------
def test_incorrect_first_answer_gets_one_retry_and_the_first_attempt_is_preserved():
    s = session()
    q = s.next_question()
    assert s.state is InterviewState.WAITING_FOR_ANSWER
    first = s.begin_attempt(q["qid"], "I would use a queue.")
    assert s.state is InterviewState.EVALUATING_ANSWER
    decision = s.finish_attempt(first.attempt_id, evaluation("incorrect", 2.0))
    assert decision["needs_retry"] and not decision["should_advance"]
    assert s.state is InterviewState.RETRYING_QUESTION and s.index == 1
    assert not s.assessments
    second = s.begin_attempt(q["qid"], "I would use an idempotency key with a unique constraint and say why.")
    decision = s.finish_attempt(second.attempt_id, evaluation("correct", 8.0, feedback="You named the mechanism."))
    assert not decision["needs_retry"] and decision["should_advance"]
    assert s.state is InterviewState.ADVANCING_QUESTION and s.index == 1
    progress = s.question_results[0]
    assert len(progress.attempts) == 2 and progress.attempts[0].answer == "I would use a queue."
    assert progress.final_score_10 == 8.0 and len(s.assessments) == 1 and s.assessments[0].attempt == 2
    assert s.next_question()["qid"] == "q2"
    with pytest.raises(InterviewError):
        s.begin_attempt("q1", "another answer")


def test_both_attempts_incorrect_advance_without_a_third_try_or_a_revealed_answer():
    s = session()
    q = s.next_question()
    a = s.begin_attempt(q["qid"], "A queue is enough.")
    d = s.finish_attempt(a.attempt_id, evaluation("incorrect", 2.0))
    assert d["needs_retry"]
    b = s.begin_attempt(q["qid"], "Use a lock and a saga.")
    d = s.finish_attempt(b.attempt_id, evaluation("incorrect", 1.5))
    assert not d["needs_retry"] and d["should_advance"] and d["next_action"] == "next_question"
    assert len(s.question_results[0].attempts) == 2 and s.question_results[0].final_score_10 == 1.5
    assert "answer" not in d["say"].lower() or "does not reveal" in d["say"].lower() or "do not reveal" in d["say"].lower()
    assert len(s.assessments) == 1 and s.assessments[0].score == 1
    with pytest.raises(InterviewError):
        s.begin_attempt(q["qid"], "A third attempt is never allowed.")


def test_partial_answer_is_acknowledged_without_pretending_it_was_complete_or_retrying():
    s = session()
    q = s.next_question()
    a = s.begin_attempt(q["qid"], "A bounded worker pool controls concurrency.")
    d = s.finish_attempt(a.attempt_id, evaluation("partially_correct", 5.5, missing=("cancellation",)))
    assert not d["needs_retry"] and d["should_advance"] and d["assessment"]["missing_concepts"] == ["cancellation"]
    assert "Do not pretend" in d["say"]


def test_duplicate_tool_events_cannot_evaluate_twice_or_advance_twice():
    s = session()
    q = s.next_question()
    a = s.begin_attempt(q["qid"], "One answer.")
    with pytest.raises(InterviewError):
        s.begin_attempt(q["qid"], "A duplicate event arrived.")
    assert s.finish_attempt(a.attempt_id, evaluation("correct", 8.0))["should_advance"]
    assert s.finish_attempt(a.attempt_id, evaluation("correct", 9.0))["status"] == "ignored"
    next_q = s.next_question()
    assert next_q["qid"] == "q2" and s.index == 2
    # A late result for the previous question cannot touch the active question.
    late = s.finish_attempt(a.attempt_id, evaluation("correct", 9.0))
    assert late["status"] == "ignored" and late["reason"] == "stale_attempt"
    assert s.current_question() is not None and s.current_question().qid == "q2"
    assert not s.current_question().finalized and s.current_question().deadline > 0
    duplicate = s.next_question()
    assert duplicate["duplicate"] and duplicate["qid"] == "q2" and s.index == 2
    s.skip_current()
    assert s.next_question()["done"]


# ---- time -----------------------------------------------------------------------------------------------------------------------------
def test_deadline_is_monotonic_records_a_timeout_and_rejects_late_answers():
    clock = Clock()
    s = session(clock=clock)
    q = s.next_question()
    clock.now = 179.0
    assert s.expire_current(qid=q["qid"]) is None
    clock.now = 180.0
    decision = s.expire_current(qid=q["qid"])
    assert decision is not None and decision["status"] == "timed_out" and decision["should_advance"]
    assert s.state is InterviewState.ADVANCING_QUESTION and s.question_results[0].timed_out
    assert s.assessments[-1].timed_out and s.assessments[-1].score == 0
    assert s.expire_current(qid=q["qid"]) is None              # idempotent
    with pytest.raises(InterviewExpired):
        s.begin_attempt(q["qid"], "This answer arrived after the deadline.")
    assert s.finish_attempt("stale", evaluation("correct", 9.0))["status"] == "ignored"


def test_answer_completed_before_deadline_may_finish_evaluating_after_it():
    clock = Clock()
    s = session(clock=clock)
    q = s.next_question()
    clock.now = 179.0
    attempt = s.begin_attempt(q["qid"], "A complete answer arrived in time.")
    clock.now = 181.0
    assert s.expire_current(qid=q["qid"]) is None              # do not turn a complete answer into a timeout
    d = s.finish_attempt(attempt.attempt_id, evaluation("correct", 8.0))
    assert d["status"] == "evaluated" and s.question_results[0].timed_out is False


def test_timer_resets_per_question_and_a_retry_uses_the_remaining_time():
    clock = Clock()
    s = session(clock=clock)
    s.next_question()
    clock.now = 30.0
    s.skip_current()
    s.next_question()
    assert s.question_results[1].deadline == 210.0
    first = s.begin_attempt("q2", "A first attempt.")
    clock.now = 205.0
    d = s.finish_attempt(first.attempt_id, evaluation("incorrect", 2.0))
    assert d["needs_retry"] is False                              # only 5 s left: no extra question budget
    assert s.question_results[1].final_score_10 == 2.0


def test_question_clock_starts_once_and_cannot_be_reset_or_rescued():
    clock = Clock()
    s = session(clock=clock)
    q = s.next_question()
    progress = s.question_results[0]
    assert progress.deadline == 180.0 and not progress.clock_started
    clock.now = 25.0
    assert s.start_question_clock(q["qid"])
    assert progress.started_at == 25.0 and progress.deadline == 205.0 and progress.clock_started
    assert progress.to_dict()["clock_started"] is True
    clock.now = 40.0
    assert not s.start_question_clock(q["qid"])                  # duplicate playback completion
    assert progress.deadline == 205.0
    assert not s.start_question_clock("q2")                       # a late callback for another question

    # An answer that starts before the question audio ends keeps the fallback deadline.
    early = session(clock=Clock())
    early_q = early.next_question()
    early.begin_attempt(early_q["qid"], "The candidate answered very quickly.")
    assert not early.start_question_clock(early_q["qid"])
    assert early.question_results[0].deadline == 180.0

    # A callback after the fallback deadline cannot rescue a timed-out question.
    late_clock = Clock()
    late = session(clock=late_clock)
    late_q = late.next_question()
    late_clock.now = 181.0
    assert not late.start_question_clock(late_q["qid"])
    assert late.question_results[0].deadline == 180.0


# ---- WorkServices / tools ------------------------------------------------------------------------------------------------------------
def _eval_llm(responses):
    def responder(req, attempt):
        if "evaluate a candidate" not in req.system:
            raise AssertionError(f"unexpected text model request: {req.system[:60]}")
        return responses.pop(0)
    return FakeLLMProvider(responder)


def _assessment(correctness: str, score: float, **kw):
    return {"correctness": correctness, "technical_accuracy": kw.get("technical", 8), "completeness": kw.get("completeness", 7),
            "reasoning": kw.get("reasoning", 7), "feedback": kw.get("feedback", "Specific feedback."),
            "hint": kw.get("hint", "Think about the failure case."), "missing_concepts": kw.get("missing", [])}


@aio
async def test_tool_flow_evaluates_meaning_retries_once_and_finishes_with_attempts_preserved(tmp_path):
    work, ex = prepared(tmp_path)
    await prepare(work, ex)
    work.begin_interview("alex-kim")
    work.evaluator = AnswerEvaluator(_eval_llm([
        _assessment("incorrect", 2.0, feedback="That would not stop duplicates."),
        _assessment("correct", 8.0, feedback="You named the key and the constraint."),
        _assessment("correct", 7.5, feedback="You described diagnosis and a fix."),
    ]), timeout_s=2)
    q1 = (await acall(ex, "interview_next_question")).output
    work.on_transcript("I would use a queue and retries.", Attribution("a", 0.99))
    first = await acall(ex, "interview_record_answer", qid=q1["qid"])
    assert first.ok and first.output["needs_retry"] and first.output["assessment"]["correctness"] == "incorrect"
    assert work.session.index == 1                                  # a retry does not advance
    work.on_transcript("I would use an idempotency key with a unique constraint.", Attribution("a", 0.99))
    second = await acall(ex, "interview_record_answer", qid=q1["qid"])
    assert second.ok and second.output["should_advance"] and "score" in second.output["assessment"]
    assert work.session.question_results[0].attempts[0].answer == "I would use a queue and retries."
    q2 = (await acall(ex, "interview_next_question")).output
    work.on_transcript("The bug was a race in the retry code; I reproduced it and fixed the guard.", Attribution("a", 0.99))
    third = await acall(ex, "interview_record_answer", qid=q2["qid"])
    assert third.ok and third.output["should_advance"]
    finished = await acall(ex, "finish_interview")
    assert finished.ok and finished.output["human_review_required"]
    result = work.last_result
    assert result is not None and result.questions_covered == 2
    assert result.question_results[0]["attempts"][0]["answer"] == "I would use a queue and retries."
    assert result.question_results[0]["final_score_10"] == 7.4
    await work.close()


@aio
async def test_question_clock_starts_on_completed_playback_and_a_retry_shares_it(tmp_path):
    clock = Clock()
    work, ex = prepared(tmp_path, monotonic=clock)
    await prepare(work, ex)
    work.begin_interview("alex-kim")
    work.evaluator = AnswerEvaluator(_eval_llm([
        _assessment("incorrect", 2.0, feedback="That would not stop duplicates."),
        _assessment("correct", 8.0, feedback="You named the key and the constraint."),
    ]), timeout_s=2)
    q1 = (await acall(ex, "interview_next_question")).output
    progress = work.session.current_question()
    assert progress is not None and progress.deadline == 180.0 and not progress.clock_started

    work.on_playback_finished(1, completed=False, cancelled=True)       # interrupted question: keep the fallback
    assert progress.deadline == 180.0 and not progress.clock_started
    clock.now = 12.0
    work.on_playback_finished(1, completed=True, cancelled=False)        # question finished playing
    assert progress.deadline == 192.0 and progress.clock_started
    work.on_playback_finished(1, completed=True, cancelled=False)        # duplicate completion
    assert progress.deadline == 192.0

    work.on_transcript("I would use a queue.", Attribution("a", 0.99))
    first = await acall(ex, "interview_record_answer", qid=q1["qid"])
    assert first.ok and first.output["needs_retry"]
    assert work.session.current_question().deadline == 192.0            # retry shares the original deadline
    clock.now = 40.0
    work.on_playback_finished(2, completed=True, cancelled=False)        # a retry prompt must not re-arm the clock
    assert work.session.current_question().deadline == 192.0

    work.on_transcript("I would use an idempotency key with a unique constraint.", Attribution("a", 0.99))
    second = await acall(ex, "interview_record_answer", qid=q1["qid"])
    assert second.ok and second.output["should_advance"]
    clock.now = 50.0
    q2 = (await acall(ex, "interview_next_question")).output
    next_progress = work.session.current_question()
    assert next_progress is not None and next_progress.qid == q2["qid"] and next_progress.deadline == 230.0
    clock.now = 60.0
    work.on_playback_finished(3, completed=True, cancelled=False)
    assert next_progress.deadline == 240.0
    await work.close()


@aio
async def test_timeout_advances_exactly_once_and_a_late_answer_cannot_move_the_next_question(tmp_path):
    clock = Clock()
    work, ex = prepared(tmp_path, monotonic=clock)
    await prepare(work, ex)
    work.begin_interview("alex-kim")
    q1 = (await acall(ex, "interview_next_question")).output
    clock.now = 181.0
    decision = work.session.expire_current(qid=q1["qid"])
    assert decision is not None and decision["next_action"] == "next_question"
    next_question = work._advance_after_timeout(decision)
    assert next_question is not None and next_question["qid"] == "q2"
    assert work.session.current_question().qid == "q2"
    timer = work._interview_timer
    assert timer is not None and not timer.cancelled()
    assert work._advance_after_timeout(decision) is None                  # stale duplicate decision
    assert work._interview_timer is timer and not timer.cancelled()        # q2 keeps its own timer
    work.on_transcript("A late answer for the previous question.", Attribution("a", 0.99))
    late = await acall(ex, "interview_record_answer", qid=q1["qid"])
    assert not late.ok
    assert work.session.current_question().qid == "q2"
    assert not work.session.current_question().finalized
    await work.close()


@aio
async def test_tool_flow_times_out_without_evaluating_and_survives_a_closed_session(tmp_path):
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    work, ex = prepared(tmp_path, speak=speak)
    await prepare(work, ex)
    work.settings = dataclasses.replace(work.settings, interview_question_limit_s=0.1, interview_answer_grace_s=0.0)
    work.begin_interview("alex-kim")
    await acall(ex, "interview_next_question")
    await asyncio.sleep(0.15)                                    # q1 expires; q2's timer starts after it
    assert work.session is not None and work.session.question_results[0].timed_out
    assert work.session.assessments[0].timed_out
    assert spoken and "three-minute limit" in spoken[0]          # the timer task must not cancel its own prompt
    assert len([text for text in spoken if "three-minute limit" in text]) == 1
    assert work.metrics.snapshot()["counters"].get("interview_question_timeouts", 0) == 1
    # The timer advanced to q2 and scheduled its own timer; closing invalidates it.
    await work.close()
    assert work._interview_timer is None or work._interview_timer.cancelled()


@aio
async def test_duplicate_begin_does_not_restart_the_question_or_reset_its_clock(tmp_path):
    clock = Clock()
    work, ex = prepared(tmp_path, monotonic=clock)
    await prepare(work, ex)
    work.begin_interview("alex-kim")
    q = (await acall(ex, "interview_next_question")).output
    clock.now = 12.0
    work.on_playback_finished(1, completed=True, cancelled=False)
    progress = work.session.current_question()
    assert progress is not None and progress.deadline == 192.0
    timer = work._interview_timer
    clock.now = 40.0
    prompt = work.begin_interview("alex-kim")
    assert "already in progress" in prompt.lower()
    assert work.session.current_question() is progress
    assert progress.qid == q["qid"] and progress.deadline == 192.0
    assert work._interview_timer is timer and timer is not None and not timer.cancelled()
    await work.close()


@aio
async def test_duplicate_transcripts_do_not_duplicate_the_answer_and_begin_is_idempotent(tmp_path):
    work, ex = prepared(tmp_path)
    await prepare(work, ex)
    first_prompt = work.begin_interview("alex-kim")
    session_id = work.session.interview_id if work.session else ""
    second_prompt = work.begin_interview("alex-kim")
    assert "already in progress" in second_prompt.lower()
    assert work.session is not None and work.session.interview_id == session_id
    q = (await acall(ex, "interview_next_question")).output
    work.on_transcript("I would use an idempotency key.", Attribution("a", 0.99))
    work.on_transcript("I would use an idempotency key.", Attribution("a", 0.99))
    assert work._live_answer == "I would use an idempotency key."
    record = TurnRecord("I would use an idempotency key.", "a", "Alex Kim", 0.99, True, "t1")
    await work.on_turn(record)
    assert work._live_answer == "I would use an idempotency key."
    work.session.skip_current()
    await work.close()
    assert first_prompt and q


@aio
async def test_live_engine_handles_a_short_pause_and_duplicate_finals_without_double_evaluation(tmp_path):
    from tara.hiring.planner import InterviewPlan, Question, default_criteria
    from tara.voice.events import InputTranscript, ToolCallRequested, ToolRequest, TurnComplete
    from tests.hiring.test_hiring import profile
    from tests.voice.test_voice_engine import hush, mic, until
    from tests.work.test_work_integration import engine_with_work

    eng, sess, work, *_ = engine_with_work(tmp_path)
    prof = profile()
    plan = InterviewPlan("Go Engineer", default_criteria(["Go"]),
                         [Question("q1", "How would you prevent duplicate payments?", "Go", "core", (),
                                   ("idempotency key", "unique constraint"))])
    work.workspace.save_candidate("alex", {"profile": prof.to_dict(), "plan": plan.to_dict()})
    work.begin_interview("alex")
    work.evaluator = AnswerEvaluator(_eval_llm([_assessment("correct", 8.0)]), timeout_s=2)
    await eng.start()
    try:
        sess.push(ToolCallRequested((ToolRequest("1", "interview_next_question", {}),)))
        await until(lambda: sess.tool_results)
        qid = sess.tool_results[0][2]["result"]["qid"]
        for ch in mic(300, "a") + hush("a", 400):
            await eng.on_meeting_audio(ch)
        answer = "I would use an idempotency key with a unique constraint."
        sess.push(InputTranscript(answer, True), InputTranscript(answer, True),       # duplicate final event
                  ToolCallRequested((ToolRequest("2", "interview_record_answer", {"qid": qid}),)))
        await until(lambda: len(sess.tool_results) >= 2)
        assert sess.tool_results[1][2]["result"]["should_advance"]
        assert work.session.question_results[0].attempts[0].answer == answer       # not doubled
        assert len(work.session.question_results[0].attempts) == 1
        assert eng.metrics.counters["live_input_transcript_duplicates"] == 1
        sess.push(ToolCallRequested((ToolRequest("3", "interview_record_answer", {"qid": qid}),)))
        await until(lambda: len(sess.tool_results) >= 3)
        assert sess.tool_results[2][2]["result"]["status"] == "already_evaluated"
        assert len(work.session.question_results[0].attempts) == 1
        sess.push(TurnComplete(1))                                                    # the committed turn arrives late
        await until(lambda: any("Candidate A" == t[0] for t in eng.working.turns))
        assert not work.session.done
    finally:
        await eng.close()
        await work.close()


def test_work_settings_reject_impossible_interview_limits(tmp_path):
    env = {"TARA_WORK_ENABLED": "1", "TARA_WORK_DIR": str(tmp_path / "w"),
           "TARA_INTERVIEW_QUESTION_LIMIT_S": "1", "TARA_INTERVIEW_RETRY_LIMIT": "9",
           "TARA_INTERVIEW_EVAL_TIMEOUT_S": "0", "TARA_INTERVIEW_ANSWER_GRACE_S": "99"}
    errs = {i.field for i in validate(RuntimeSettings.from_env(env)) if i.severity == "error"}
    assert {"TARA_INTERVIEW_QUESTION_LIMIT_S", "TARA_INTERVIEW_RETRY_LIMIT",
            "TARA_INTERVIEW_EVAL_TIMEOUT_S", "TARA_INTERVIEW_ANSWER_GRACE_S"} <= errs
