import asyncio
from pathlib import Path

from helpers import DISCLOSURE, ROLES, TH
from test_hiring import CV_TEXT, gh_http
from test_work_integration import acall, aio, prepare

from tara.classify import RuleIntentClassifier
from tara.config import MeetingConfig, Mode, ParticipantSpec, Role, SpeakingPolicy
from tara.hiring.github import GitHubReader
from tara.hiring.intake import CandidateBuilder
from tara.hiring.planner import QuestionPlanner
from tara.hiring.wizard import (
    Wizard,
    clean_path,
    list_interviews,
    new_code,
    parse_amount,
    parse_skills,
)
from tara.hiring.workspace import Workspace
from tara.participation import Attribution
from tara.voice.participation import LiveParticipation, Verdict

run = asyncio.run


def test_terminal_drag_and_drop_paths_are_cleaned():
    assert clean_path("/Users/me/My\\ CV.pdf") == Path("/Users/me/My CV.pdf")
    assert clean_path("'/Users/me/My CV.pdf'") == Path("/Users/me/My CV.pdf")
    assert clean_path("file:///Users/me/My%20CV.pdf") == Path("/Users/me/My CV.pdf")
    assert clean_path('"~/cv.pdf"') == Path("~/cv.pdf").expanduser() and clean_path("   ") is None


def test_amount_is_understood_in_plain_words():
    assert parse_amount("2 easy") == (2, "entry")
    assert parse_amount("ask him two easy questions") == (2, "entry")
    assert parse_amount("six hard") == (6, "senior")
    assert parse_amount("") == (5, "intermediate") and parse_amount("40 medium") == (12, "intermediate")
    assert parse_skills("Python, SQL; system design\nGo") == ["Python", "SQL", "system design", "Go"]


def test_codes_are_unique():
    assert new_code("Feremi Oresajo", set()) == "feremi-oresajo"
    c = new_code("Feremi Oresajo", {"feremi-oresajo"})
    assert c != "feremi-oresajo" and c.startswith("feremi-oresajo-") and len(c) <= 40


def _wizard(tmp_path, answers):
    ws = Workspace(tmp_path / "work")
    ws.ensure()
    said: list[str] = []
    asked: list[str] = []
    it = iter(answers)

    def ask(prompt):
        asked.append(prompt)
        return next(it)

    builder = CandidateBuilder(ws.inbox, None, None, GitHubReader(gh_http()))
    return Wizard(ws, builder, QuestionPlanner(None), ask, said.append), ws, said, asked


def test_wizard_collects_sources_says_it_has_enough_context_asks_amount_and_gives_a_code(tmp_path):
    cv = tmp_path / "My CV.txt"
    cv.write_text(CV_TEXT)
    wiz, ws, said, asked = _wizard(tmp_path, ["Go Backend Engineer", "Go, PostgreSQL", "alexdev", "", f"'{cv}'", "2 easy"])
    out = run(wiz.create("Alex Kim"))
    assert out is not None and out["code"] == "alex-kim" and len(out["plan"].questions) == 2 and out["difficulty"] == "entry"
    assert any("gotten enough context about Alex Kim" in t for t in said)
    assert any("How many questions" in a for a in asked)
    saved = ws.load_candidate("alex-kim")
    assert saved["requirements"] == ["Go", "PostgreSQL"] and saved["difficulty"] == "entry" and saved["plan"]["questions"]
    assert (ws.inbox / "alex-kim-cv.txt").is_file()                                    # the dropped CV was copied into the inbox
    assert [r.split()[0] for r in list_interviews(ws)] == ["alex-kim"] and "not run yet" in list_interviews(ws)[0]


def test_wizard_second_candidate_with_the_same_name_gets_a_different_code(tmp_path):
    wiz, ws, *_ = _wizard(tmp_path, ["Backend", "Go", "alexdev", "", "", "3 medium", "Backend", "Go", "alexdev", "", "", "3 medium"])
    a = run(wiz.create("Alex Kim"))
    b = run(wiz.create("Alex Kim"))
    assert a["code"] != b["code"] and sorted(ws.list_candidates()) == sorted([a["code"], b["code"]])


def test_wizard_with_no_usable_source_saves_nothing_and_says_why(tmp_path):
    bad = tmp_path / "photo.png"
    bad.write_bytes(b"x")
    wiz, ws, said, _ = _wizard(tmp_path, ["Backend", "Go", "", "", str(bad), ""] + [""] * 12)
    assert run(wiz.create("Zed")) is None
    assert any("can read" in t for t in said) and any("nothing to read" in t.lower() for t in said) and ws.list_candidates() == []


def test_wizard_with_little_context_offers_role_only_questions(tmp_path):
    # an invalid GitHub name yields almost nothing: decline to add a source, accept role-only questions
    wiz, ws, said, _ = _wizard(tmp_path, ["Backend", "Go", "not a valid user!!", "", "", "n", "y", "2 easy"])
    out = run(wiz.create("Zed"))
    assert out is not None and len(out["plan"].questions) == 2
    assert any("only found a little" in t for t in said)


def test_wizard_can_be_declined_and_saves_nothing(tmp_path):
    wiz, ws, said, _ = _wizard(tmp_path, ["Backend", "Go", "not a valid user!!", "", "", "n", "n"])
    assert run(wiz.create("Nobody")) is None and ws.list_candidates() == [] and any("nothing was saved" in t.lower() for t in said)


# ---- running the interview -------------------------------------------------------------------------------------
@aio
async def test_autopilot_interview_runs_without_a_spoken_role_and_finishes_only_when_done(tmp_path):
    from test_work_integration import prepared
    work, ex = prepared(tmp_path)
    await prepare(work, ex)
    prompt = work.begin_interview("alex-kim")
    assert "Alex Kim" in prompt and "You are Alex Kim, right?" in prompt and "interview_next_question" in prompt and "AI" in prompt
    work.on_transcript("anything", Attribution("a", 0.99))                  # the candidate speaking: not an organizer
    q1 = (await acall(ex, "interview_next_question")).output
    work.working.add_turn("Tara", q1["question"])
    work.working.add_turn("a", "I would use an idempotency key with a unique constraint and a transaction.")
    rec = await acall(ex, "interview_record_answer", qid=q1["qid"], criterion=q1["criterion"], score=3, quote="idempotency key with a unique constraint")
    assert rec.ok
    early = await acall(ex, "finish_interview")
    assert not early.ok and early.error == "not_finished_yet" and "questions left" in early.details["reason"]
    q2 = (await acall(ex, "interview_next_question")).output
    assert q2["qid"] == "q2"
    assert (await acall(ex, "interview_next_question")).output["done"] is True
    fin = await acall(ex, "finish_interview")
    assert fin.ok and fin.output["human_review_required"] is True
    await work.close()


@aio
async def test_ending_an_interview_early_saves_the_evidence_flags_it_and_writes_a_report(tmp_path):
    from test_work_integration import prepared
    work, ex = prepared(tmp_path)
    await prepare(work, ex)
    work.begin_interview("alex-kim")
    q1 = (await acall(ex, "interview_next_question")).output
    work.working.add_turn("Tara", q1["question"])
    work.working.add_turn("a", "I would use an idempotency key with a unique constraint and a transaction.")
    await acall(ex, "interview_record_answer", qid=q1["qid"], criterion=q1["criterion"], score=3, quote="idempotency key with a unique constraint")
    out = await work.end_interview()                                         # the person closed the program mid-interview
    assert out["interview_id"] and any("planned questions were asked" in f for f in out["flags"])
    assert Path(out["report_path"]).is_file() and out["report_file"].endswith(".docx")
    assert work.workspace.list_interviews()
    assert (await work.end_interview())["interview_id"]                      # safe to call twice
    await work.close()


def _interview_cfg(**kw):
    return MeetingConfig(
        purpose="Interview for Backend", mode=Mode.INTERVIEW_STRUCTURED, duration_min=30, policy=SpeakingPolicy.ON_ADDRESS_ONLY,
        participants=(ParticipantSpec("a", "Alex", Role.CANDIDATE), ParticipantSpec("r", "Recruiter", Role.RECRUITER)),
        disclosure_text=DISCLOSURE, **kw)


def test_a_tara_led_interview_answers_the_candidate_without_her_name_but_not_others():
    async def go():
        c = _interview_cfg(lead_interview=True)
        gate = LiveParticipation(c, ROLES, RuleIntentClassifier(), TH, identity_available=True)
        d = await gate.evaluate("I would use a queue and retry with backoff", Attribution("a", 0.99))
        assert d.verdict is Verdict.ALLOW and d.reason == "candidate_turn"
        assert (await gate.evaluate("I would use a queue", Attribution("a", 0.2))).verdict is Verdict.DENY      # identity unreliable
        assert (await gate.evaluate("side chat between the team", Attribution("r", 0.99))).verdict is Verdict.DENY
        plain = LiveParticipation(_interview_cfg(), ROLES, RuleIntentClassifier(), TH, identity_available=True)
        assert (await plain.evaluate("I would use a queue", Attribution("a", 0.99))).verdict is Verdict.DENY    # default unchanged
        muted = await gate.evaluate("I would use a queue", Attribution("a", 0.99), muted=True)
        assert muted.verdict is Verdict.DENY                                                                    # mute still wins
    run(go())


def test_planner_allows_a_two_question_interview():
    from tara.hiring.profile import CandidateProfile
    plan = run(QuestionPlanner(None).plan(CandidateProfile("a", "Alex"), "Backend", ["Go", "SQL"], n=2))
    assert len(plan.questions) == 2


def _app(tmp_path, code):
    from test_work_integration import LIVE

    from tara.adapters.local import LocalMeetingAdapter
    from tara.settings import RuntimeSettings
    from tara.voice.app import LiveTaraApp
    from tara.voice.fakes import FakeVoiceProvider, FakeVoiceSession
    from tara.voice.memory import Scope
    env = {**LIVE, "TARA_WORK_ENABLED": "1", "TARA_WORK_DIR": str(tmp_path / "work")}
    sess = FakeVoiceSession()
    c = _interview_cfg(lead_interview=True)
    app = LiveTaraApp(RuntimeSettings.from_env(env), c, {"a": Role.CANDIDATE, "r": Role.RECRUITER}, {"a": "Alex Kim", "r": "Recruiter"},
                      LocalMeetingAdapter(), session_id="x", scope=Scope("t1", "interview-alex-kim"), provider=FakeVoiceProvider([sess]),
                      interview_code=code)
    return app, sess


@aio
async def test_running_an_interview_makes_tara_speak_first_and_a_wrong_code_fails_loudly(tmp_path):
    import pytest as _pytest
    from test_work_integration import prepared

    from tara.errors import ConfigError
    work, ex = prepared(tmp_path)
    await prepare(work, ex)                                      # saves candidate alex-kim under tmp_path/work
    app, sess = _app(tmp_path, "alex-kim")
    await app.start()
    await asyncio.sleep(1.4)                                     # the opening goes out after a short pause
    assert any("You are Alex Kim, right?" in t for t in sess.texts) and app.work.autopilot is True
    await app.close()
    assert app.last_finalize.get("interview_id")                 # closing writes the result and the report even with no answers yet
    bad, _ = _app(tmp_path, "nobody-here")
    with _pytest.raises(ConfigError):
        await bad.start()
    await bad.close()
