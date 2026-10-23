"""WorkServices through the real ToolExecutor and the Live engine: permissions, interviews, documents, auto memory, nudges."""
import asyncio
import json
from pathlib import Path

from tara.adapters.local import LocalMeetingAdapter
from tara.control.state import ControlState
from tara.core.config import ToolSpec
from tara.core.settings import RuntimeSettings, WorkSettings, validate
from tara.floor.classify import RuleIntentClassifier
from tara.floor.participation import Attribution
from tara.hiring.github import GitHubReader
from tara.memory import InMemoryKnowledgeStore, KnowledgeService
from tara.memory.models import MemoryScope
from tara.memory.service import MemoryService
from tara.memory.store import InMemoryMemoryStore
from tara.testing.fakes import FakeLLMProvider
from tara.testing.voice_fakes import FakeVoiceProvider, FakeVoiceSession
from tara.tools.executor import ToolExecutor
from tara.tools.utility.contracts import ResearchResult, ResearchSource
from tara.voice.app import LiveTaraApp
from tara.voice.engine import LiveParams, LiveVoiceEngine
from tara.voice.events import InputTranscript, ToolCallRequested, ToolRequest, TurnComplete, VoiceSessionConfig
from tara.voice.memory import InMemoryMeetingMemory, Scope, WorkingMemory
from tara.voice.participation import LiveParticipation
from tara.voice.playback import PlaybackResult
from tara.voice.registry import SessionRegistry
from tara.work import WorkServices
from tests.helpers import ROLES, TH, cfg
from tests.hiring.test_hiring import CV_TEXT, EXTRACTION, GOOD_PLAN, gh_http
from tests.voice.test_voice_engine import NAMES, hush, mic, until

run = asyncio.run
SC = Scope("t1", "m1")


def llm_for(texts):
    """A fake text model: JSON for the extractor/planner, Markdown for documents."""
    def responder(req, attempt):
        if "Extract a structured candidate profile" in req.system:
            return EXTRACTION
        if "design structured interview questions" in req.system:
            return GOOD_PLAN
        return texts.get("doc", "# Report\n\n## Summary\nWritten from the facts.\n")
    return FakeLLMProvider(responder)


def make_work(tmp_path, *, c=None, session_id="s1", llm=None, notes=None, memory=None, working=None, **kw):
    memory = memory if memory is not None else InMemoryMeetingMemory()
    working = working if working is not None else WorkingMemory()
    work = WorkServices(
        WorkSettings(enabled=True, dir=str(tmp_path / "work"), job_timeout_s=20), session_id=session_id, scope=SC, cfg=c or cfg(),
        roles=ROLES, names=NAMES, meeting_memory=memory, working=working, control=ControlState(), min_conf=0.8,
        llm=llm if llm is not None else llm_for({}), fetcher=None, github=GitHubReader(gh_http()),
        send_note=(notes.append if notes is not None else None), candidate_speakers=frozenset({"a"}), **kw)
    ex = ToolExecutor({}, ())
    for d in work.tool_defs:
        ex.register_and_grant(d, ToolSpec(d.name, d.permission or "work", False))
    return work, ex


async def acall(ex, name, **args):
    from tara.core.contracts import ToolCall
    return await ex.execute(ToolCall(name, args), f"c-{name}")


def aio(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.run(fn(*a, **k))
    return wrapper


def authorize(work, who="r", conf=0.99):
    work.on_transcript("anything", Attribution(who, conf))


async def finish_job(work, job_id):
    job = work.jobs.get("s1", job_id)
    await job.task
    return job


def test_catalog_lists_only_enabled_features_and_marks_writes(tmp_path):
    work, ex = make_work(tmp_path)
    names = {d["name"]: d for d in ex.catalog()}
    assert {"recall_meeting", "meeting_recap", "create_document", "create_document_from_source", "job_status", "list_candidates", "get_candidate", "prepare_candidate",
            "start_interview", "interview_next_question", "interview_record_answer", "finish_interview",
            "recap_meeting", "set_meeting_type", "add_vocabulary", "set_agenda", "agenda_status", "agenda_mark"} == set(names)
    assert not names["recall_meeting"]["side_effect"] and names["create_document"]["side_effect"] and names["create_document"]["latency"] == "background"
    source_schema = next(d for d in work.tool_defs if d.name == "create_document_from_source").parameters_schema
    assert source_schema["properties"]["source_mode"]["enum"] == ["meeting", "provided_material"]
    off = WorkServices(WorkSettings(enabled=True, dir=str(tmp_path / "w2"), hiring=False, documents=False), session_id="s", scope=SC, cfg=cfg(),
                       roles=ROLES, names=NAMES, meeting_memory=InMemoryMeetingMemory(), working=WorkingMemory(), control=ControlState(), min_conf=0.8)
    assert {d.name for d in off.tool_defs} == {"recall_meeting", "meeting_recap", "recap_meeting", "set_meeting_type", "add_vocabulary",
                                                "set_agenda", "agenda_status", "agenda_mark"}
    assert "create_document" not in off.instructions() and "interview" not in off.instructions().lower()
    assert "never claim a document exists" in work.instructions().lower() or "Never claim a document exists" in work.instructions()


@aio
async def test_nothing_that_starts_or_writes_works_without_a_recruiter_or_organizer(tmp_path):
    work, ex = make_work(tmp_path)
    for name, args in (("create_document", dict(kind="minutes", title="T")), ("start_interview", dict(candidate_id="alex")),
                       ("finish_interview", {}), ("prepare_candidate", dict(label="A", role_title="r", requirements="Go", github="alexdev"))):
        res = await acall(ex, name, **args)
        assert not res.ok and res.error == "not_authorized" and "organizer or recruiter" in res.details["reason"], (name, res.error)
    authorize(work, "a")                                                    # a candidate speaking
    assert not (await acall(ex, "create_document", kind="minutes", title="T")).ok
    authorize(work, None, 0.0)                                              # unknown speaker
    assert not (await acall(ex, "create_document", kind="minutes", title="T")).ok
    authorize(work, "r", 0.3)                                               # recruiter but unreliable identity
    assert not (await acall(ex, "create_document", kind="minutes", title="T")).ok
    authorize(work, "r")
    assert (await acall(ex, "create_document", kind="minutes", title="T")).ok


@aio
async def test_prepare_candidate_runs_in_the_background_and_is_readable_afterwards(tmp_path):
    work, ex = make_work(tmp_path)
    (work.workspace.inbox).mkdir(parents=True)
    (work.workspace.inbox / "alex.txt").write_text(CV_TEXT)
    authorize(work)
    started = await acall(ex, "prepare_candidate", label="Alex Kim", role_title="Go Backend Engineer", requirements="Go, PostgreSQL",
                   cv_file="alex.txt", github="alexdev")
    assert started.ok and started.output["status"] == "started"
    job = await finish_job(work, started.output["job_id"])
    assert job.state == "completed" and job.result["candidate_id"] == "alex-kim" and job.result["questions"] == 2
    assert (await acall(ex, "job_status", job_id=job.job_id)).output["state"] == "completed"
    listed = (await acall(ex, "list_candidates")).output
    assert listed["candidates"] == [{"candidate_id": "alex-kim", "role": "Go Backend Engineer", "questions": 2}]
    got = (await acall(ex, "get_candidate", candidate_id="alex-kim")).output
    assert "Alex" not in got["brief"] and "Skills claimed" in got["brief"] and got["question_count"] == 2
    assert not (await acall(ex, "get_candidate", candidate_id="nobody")).ok and not (await acall(ex, "get_candidate", candidate_id="../etc")).ok
    for bad in (dict(label="A", role_title="r", requirements=""), dict(label="A", role_title="r", requirements="Go")):
        assert not (await acall(ex, "prepare_candidate", **bad)).ok                  # no skills / no sources are refused up front
    for path_attack in ("../secret.txt", "/etc/passwd"):
        s2 = await acall(ex, "prepare_candidate", label="Zed", role_title="r", requirements="Go", cv_file=path_attack)
        j2 = await finish_job(work, s2.output["job_id"])
        assert j2.state == "completed" and any("CV:" in w for w in j2.result["warnings"])    # warned, never read
    await work.close()
def prepared(tmp_path, **kw):
    work, ex = make_work(tmp_path, **kw)
    (work.workspace.inbox).mkdir(parents=True, exist_ok=True)
    (work.workspace.inbox / "alex.txt").write_text(CV_TEXT)
    return work, ex


async def prepare(work, ex):
    authorize(work)
    j = await acall(ex, "prepare_candidate", label="Alex Kim", role_title="Go Backend Engineer", requirements="Go, PostgreSQL", cv_file="alex.txt", github="alexdev")
    await finish_job(work, j.output["job_id"])


@aio
async def test_full_interview_flow_with_evidence_checks_and_report(tmp_path):
    work, ex = prepared(tmp_path)
    await prepare(work, ex)
    assert not (await acall(ex, "interview_next_question")).ok                      # nothing started yet
    started = await acall(ex, "start_interview", candidate_id="alex-kim")
    assert started.ok and started.output["questions"] == 2 and "Go" in started.output["criteria"]
    assert not (await acall(ex, "start_interview", candidate_id="alex-kim")).ok      # one at a time
    q1 = (await acall(ex, "interview_next_question")).output
    assert q1["qid"] == "q1" and q1["criterion"] == "Go"
    work.working.add_turn("Tara", q1["question"])
    work.working.add_turn("a", "I would use an idempotency key with a unique constraint and a transaction.")
    bad = await acall(ex, "interview_record_answer", qid="q1", criterion="Go", score=3, quote="I would use a saga and two phase commit")
    assert not bad.ok and bad.error == "interview_error" and "does not appear" in bad.details["reason"]
    ok = await acall(ex, "interview_record_answer", qid="q1", criterion="Go", score=3, rationale="named the key and constraint",
              quote="idempotency key with a unique constraint")
    assert ok.ok, (ok.error, ok.details)
    assert ok.output["attribution"] in ("verified", "unverified")
    assert not (await acall(ex, "interview_record_answer", qid="q1", criterion="Go", score=9, quote="idempotency key with a unique constraint")).ok
    done = await acall(ex, "finish_interview")
    assert done.ok and done.output["human_review_required"] and any("insufficient evidence" in f for f in done.output["flags"])
    assert "recommend" not in json.dumps(done.output).lower().replace("do not say hire or reject", "")
    saved = work.workspace.load_interview(done.output["interview_id"])
    assert saved["result"]["candidate_id"] == "alex-kim" and saved["plan"]["questions"] and "Alex" not in saved["brief"]
    assert not (await acall(ex, "interview_next_question")).ok                      # finished
    doc = await acall(ex, "create_document", kind="interview_report", title="Interview report", format="docx")
    job = await finish_job(work, doc.output["job_id"])
    assert job.state == "completed" and job.result["file"].endswith(".docx") and Path(job.result["path"]).is_file()
    import docx
    text = "\n".join(p.text for p in docx.Document(job.result["path"]).paragraphs)
    assert "Written from the facts" in text and "drafted by an AI" in text
    await work.close()
@aio
async def test_a_candidate_cannot_end_the_interview_and_quotes_must_be_from_the_candidate(tmp_path):
    work, ex = prepared(tmp_path)
    await prepare(work, ex)
    await acall(ex, "start_interview", candidate_id="alex-kim")
    q = (await acall(ex, "interview_next_question")).output
    work.working.add_turn("Candidate B", "A queue is better for the whole flow, I think.")
    work.working.add_turn("a", "I would use an idempotency key with a unique constraint.")
    rejected = await acall(ex, "interview_record_answer", qid=q["qid"], criterion="Go", score=4, quote="A queue is better for the whole flow")
    assert not rejected.ok and "not the candidate" in rejected.details["reason"]
    authorize(work, "a")
    assert not (await acall(ex, "finish_interview")).ok and work.session is not None and not work.session.done
    authorize(work, "r")
    assert (await acall(ex, "finish_interview")).ok
    await work.close()
@aio
async def test_documents_report_honest_failures_and_isolation(tmp_path):
    work, ex = make_work(tmp_path)
    authorize(work)
    assert not (await acall(ex, "create_document", kind="poem", title="T")).ok
    assert not (await acall(ex, "create_document", kind="minutes", title="T", format="exe")).ok
    assert not (await acall(ex, "create_document", kind="minutes", title="  ")).ok
    assert not (await acall(ex, "create_document", kind="interview_report", title="T")).ok       # no interview yet
    r = await acall(ex, "create_document", kind="minutes", title="Weekly sync", request="short", format="pdf")
    job = await finish_job(work, r.output["job_id"])
    assert job.state == "completed" and job.result["file"].endswith(".pdf") and job.result["written_by_model"] is True
    assert Path(job.result["path"]).read_bytes().startswith(b"%PDF")
    assert not (await acall(ex, "job_status", job_id="nope")).ok
    other, ex2 = make_work(tmp_path, session_id="s2")
    assert not (await acall(ex2, "job_status", job_id=job.job_id)).ok                           # another session cannot see this job
    no_model, ex3 = make_work(tmp_path, llm=None)
    no_model.llm = None
    from tara.docgen.composer import DocumentComposer
    no_model.composer = DocumentComposer(None)
    authorize(no_model)
    j = await acall(ex3, "create_document", kind="summary", title="Fallback", format="md")
    job3 = await finish_job(no_model, j.output["job_id"])
    assert job3.state == "completed" and job3.result["written_by_model"] is False and "no text model" in job3.result["note"]
    assert "Fallback" in Path(job3.result["path"]).read_text()
    for w in (work, other, no_model):
        await w.close()


@aio
async def test_research_document_uses_web_evidence_not_meeting_memory(tmp_path):
    class FakeResearch:
        def __init__(self):
            self.calls = []

        async def run(self, query, *, max_sources=None, max_chars=None):
            self.calls.append((query, max_sources, max_chars))
            if query == "no results":
                    return ResearchResult(query=query, queries=(query,), sources=(), provider="fake")
            return ResearchResult(
                query=query, queries=(query,), provider="fake",
                sources=(ResearchSource("s1", "AI agents overview", "https://example.com/agents",
                                        snippet="Agents use tools to act toward goals.",
                                        text="An AI agent observes its environment and takes actions toward a goal.",
                                        fetched=True),))

    prompts = []

    def write_document(req, _attempt):
        prompts.append(req.user)
        return "# AI agents\n\nAn AI agent observes its environment and acts toward a goal [s1]."

    memory_service = MemoryService(InMemoryMemoryStore())
    memory_scope = MemoryScope("t1", "u1", meeting_id="m1")
    await memory_service.remember(memory_scope, "MEETING_ONLY_canary: unrelated project decision.",
                                  source="s1:turn:1", session_id="s1")
    research = FakeResearch()
    work, ex = make_work(tmp_path, llm=FakeLLMProvider(write_document), research_service=research,
                         memory_service=memory_service, memory_scope=memory_scope)
    authorize(work)
    try:
        started = await acall(ex, "create_document_from_source", source_mode="web_research", topic="Explain AI agents",
                      title="AI agents", audience="New developers", purpose="Explain the concept",
                      length="brief", format="md")
        assert started.ok and started.output["status"] == "started"
        job = await finish_job(work, started.output["job_id"])
        assert job.state == "completed" and job.result["source_count"] == 1
        assert research.calls == [("Explain AI agents", 5, 20_000)]
        assert "observes its environment" in prompts[0] and "MEETING_ONLY_canary" not in prompts[0]
        assert "If sources disagree, describe the differing evidence" in prompts[0]
        content = Path(job.result["path"]).read_text()
        assert "acts toward a goal [s1]" in content
        assert "Sources" in content and "https://example.com/agents" in content
        assert "MEETING_ONLY_canary" not in content

        empty = await acall(ex, "create_document_from_source", source_mode="web_research", topic="no results",
                    title="No sources", audience="Developers", purpose="Explain the topic", length="brief")
        assert empty.ok
        empty_job = await finish_job(work, empty.output["job_id"])
        assert empty_job.state == "failed" and empty_job.error == "ToolError"
    finally:
        await work.close()


@aio
async def test_tool_catalog_document_lists_active_tools_without_meeting_notes(tmp_path):
    memory_service = MemoryService(InMemoryMemoryStore())
    memory_scope = MemoryScope("t1", "u1", meeting_id="m1")
    await memory_service.remember(memory_scope, "MEETING_ONLY_canary: unrelated project decision.",
                                  source="s1:turn:1", session_id="s1")
    inventory = [
        {"name": "web_search", "description": "Search current public web pages.", "params": {"query": "str"},
         "required": ["query"], "read_only": True},
        {"name": "meeting_lookup", "description": "Read a previous meeting record.", "params": {"which": "str"},
         "required": [], "read_only": True},
    ]
    work, ex = make_work(tmp_path, memory_service=memory_service, memory_scope=memory_scope,
                         tool_inventory=lambda: inventory)
    authorize(work)
    try:
        started = await acall(ex, "create_document_from_source", source_mode="tool_catalog", title="Tara tools",
                      audience="Developers", purpose="Review available integrations", length="standard", format="md")
        assert started.ok and started.output["status"] == "started"
        job = await finish_job(work, started.output["job_id"])
        assert job.state == "completed" and job.result["tool_count"] == 2
        content = Path(job.result["path"]).read_text()
        assert "web_search" in content and "meeting_lookup" in content
        assert "Search current public web pages." in content and "Parameters: query; required: query" in content
        assert "MEETING_ONLY_canary" not in content
    finally:
        await work.close()


@aio
async def test_approved_knowledge_document_uses_only_accessible_approved_chunks(tmp_path):
    memory_scope = MemoryScope("t1", "u1", meeting_id="m1")
    knowledge = KnowledgeService(InMemoryKnowledgeStore())
    await knowledge.ingest_text(memory_scope, "Approved AI policy", "Approved agents may use company APIs.",
                                source="policy.md", section_id="m1", approved=True)
    await knowledge.ingest_text(memory_scope, "Unapproved AI policy", "UNAPPROVED_canary hidden text.",
                                source="draft.md", section_id="m1", approved=False)
    await knowledge.ingest_text(memory_scope, "Other section", "OTHER_SECTION_canary hidden text.",
                                source="other.md", section_id="another-section", approved=True)
    memory_service = MemoryService(InMemoryMemoryStore())
    await memory_service.remember(memory_scope, "MEETING_ONLY_canary unrelated decision.",
                                  source="s1:turn:1", session_id="s1")
    prompts = []

    def write_document(req, _attempt):
        prompts.append(req.user)
        return "# AI policy\n\nApproved agents may use company APIs [k1]."

    work, ex = make_work(tmp_path, llm=FakeLLMProvider(write_document), knowledge_service=knowledge,
                         memory_service=memory_service, memory_scope=memory_scope)
    authorize(work)
    try:
        started = await acall(ex, "create_document_from_source", source_mode="approved_knowledge",
                              topic="approved agents company APIs", title="AI policy", audience="Engineers",
                              purpose="Explain approved API use", length="brief", format="md")
        assert started.ok
        job = await finish_job(work, started.output["job_id"])
        assert job.state == "completed" and job.result["source_count"] == 1
        assert "Approved agents may use company APIs." in prompts[0]
        for forbidden in ("UNAPPROVED_canary", "OTHER_SECTION_canary", "MEETING_ONLY_canary"):
            assert forbidden not in prompts[0]
        content = Path(job.result["path"]).read_text()
        assert "[k1]" in content and "policy.md" in content
    finally:
        await work.close()


@aio
async def test_provided_material_document_is_confined_to_inbox_and_uses_brief(tmp_path):
    from tara.docgen.composer import DocumentComposer, LlmTextGenerator

    work, ex = make_work(tmp_path)
    authorize(work)
    work.workspace.inbox.mkdir(parents=True, exist_ok=True)
    supplied = work.workspace.inbox / "agent-notes.md"
    supplied.write_text("Age: 32\nContact: alice@example.com\nAn agent selects tools and acts toward a defined goal.", encoding="utf-8")
    outside = tmp_path / "private.txt"
    outside.write_text("OUTSIDE_INBOX_canary", encoding="utf-8")
    prompts = []

    def write_document(req, _attempt):
        prompts.append(req.user)
        return "# Agents\n\nAn agent selects tools and acts toward a goal [f1]."

    work.llm = FakeLLMProvider(write_document)
    work.composer = DocumentComposer(LlmTextGenerator(work.llm))
    try:
        started = await acall(ex, "create_document_from_source", source_mode="provided_material",
                              file_name="agent-notes.md", title="Agents", audience="Product team",
                              purpose="Explain these notes", length="brief", format="md")
        assert started.ok
        job = await finish_job(work, started.output["job_id"])
        assert job.state == "completed" and job.result["source_count"] == 1
        assert "selects tools and acts toward a defined goal" in prompts[0]
        assert "Product team" in prompts[0] and "Explain these notes" in prompts[0] and "brief" in prompts[0]
        assert "alice@example.com" not in prompts[0] and "Age: 32" not in prompts[0]
        assert "[email removed]" in prompts[0] and "OUTSIDE_INBOX_canary" not in prompts[0]
        generated = Path(job.result["path"]).read_text()
        assert "[f1]" in generated and "work/inbox/agent-notes.md" in generated

        denied_opt_in = await acall(ex, "create_document_from_source", source_mode="provided_material",
                        file_name="agent-notes.md", title="Agents with details", audience="Product team",
                        purpose="Explain these notes", length="brief", preserve_personal_details=True)
        assert not denied_opt_in.ok and denied_opt_in.error == "personal_details_opt_in_required"

        work.on_transcript("Please include personal details as written.", Attribution("r", 0.99))
        preserved = await acall(ex, "create_document_from_source", source_mode="provided_material",
                    file_name="agent-notes.md", title="Agents with details", audience="Product team",
                    purpose="Explain these notes", length="brief", preserve_personal_details=True)
        assert preserved.ok
        preserved_job = await finish_job(work, preserved.output["job_id"])
        assert preserved_job.state == "completed" and "alice@example.com" in prompts[-1]

        missing_audience = await acall(ex, "create_document_from_source", source_mode="meeting", title="Missing brief",
                           audience="", purpose="Create a meeting brief", length="brief")
        assert not missing_audience.ok and missing_audience.error == "missing_audience"

        rejected = await acall(ex, "create_document_from_source", source_mode="provided_material",
                               file_name="../private.txt", title="Outside", audience="Team",
                               purpose="Use outside file", length="brief", format="md")
        assert rejected.ok
        rejected_job = await finish_job(work, rejected.output["job_id"])
        assert rejected_job.state == "failed" and rejected_job.error == "ToolError"
    finally:
        await work.close()


@aio
async def test_meeting_documents_are_scoped_to_current_or_selected_session(tmp_path):
    memory_scope = MemoryScope("t1", "u1", meeting_id="m1")
    memory_service = MemoryService(InMemoryMemoryStore())
    for number in (1, 2, 3):
        session_id = f"run-{number}"
        await memory_service.start_session(memory_scope, session_id)
        await memory_service.remember(memory_scope, f"MEETING_{number}_canary: summary for meeting {number}.",
                                      source=f"{session_id}:summary", kind="summary", session_id=session_id)
        if number < 3:
            await memory_service.end_session(memory_scope, session_id)

    prompts = []

    def write_document(req, _attempt):
        prompts.append(req.user)
        return "# Meeting document\n\nSummary from the selected meeting."

    work, ex = make_work(tmp_path, session_id="run-3", llm=FakeLLMProvider(write_document),
                         memory_service=memory_service, memory_scope=memory_scope)
    authorize(work)
    try:
        for selector, title, included, excluded in (
            ("current", "Current", "MEETING_3_canary", ("MEETING_1_canary", "MEETING_2_canary")),
            ("previous", "Previous", "MEETING_2_canary", ("MEETING_1_canary", "MEETING_3_canary")),
            ("1", "First", "MEETING_1_canary", ("MEETING_2_canary", "MEETING_3_canary")),
        ):
            started = await acall(ex, "create_document_from_source", source_mode="meeting", meeting_selector=selector,
                                  kind="summary", title=title, audience="Team", purpose="Review the meeting",
                                  length="brief", format="md")
            assert started.ok
            job = work.jobs.get("run-3", started.output["job_id"])
            await job.task
            assert job.state == "completed"
            assert included in prompts[-1]
            assert all(value not in prompts[-1] for value in excluded)
    finally:
        await work.close()


@aio
async def test_busy_job_board_is_reported_not_queued_forever(tmp_path):
    gate = asyncio.Event()

    class Slow(FakeLLMProvider):
        async def generate(self, req, parse, schema=None):
            await gate.wait()
            return "# T\n\nbody"
    work, ex = make_work(tmp_path, llm=Slow(lambda r, a: ""))
    work.jobs.max_running = 1
    authorize(work)
    first = await acall(ex, "create_document", kind="summary", title="One")
    second = await acall(ex, "create_document", kind="summary", title="Two")
    assert first.ok and not second.ok and second.error == "busy" and second.retryable
    gate.set()
    await finish_job(work, first.output["job_id"])
    await work.close()
# ---- through the Live engine ---------------------------------------------------------------------------------------------------------
def engine_with_work(tmp_path, c=None, llm=None):
    c = c or cfg()
    notes_sink: list[str] = []
    mem, working = InMemoryMeetingMemory(), WorkingMemory()
    store = InMemoryMemoryStore()
    svc = MemoryService(store)
    holder: dict = {}
    work, ex = make_work(tmp_path, c=c, llm=llm, memory=mem, working=working, memory_service=svc, memory_scope=MemoryScope("t1", "u1"),
                         notes=notes_sink)
    work.nudger.send_note = lambda n: holder["eng"].queue_note(n)
    sess = FakeVoiceSession()
    eng = LiveVoiceEngine(
        session_id="s1", scope=SC, cfg=c, roles=ROLES, names=NAMES, provider=FakeVoiceProvider([sess]),
        session_config=VoiceSessionConfig("sys"), adapter=LocalMeetingAdapter(realtime_factor=0.1),
        participation=LiveParticipation(c, ROLES, RuleIntentClassifier(), TH), registry=SessionRegistry(2), tools=ex,
        working=working, meeting_memory=mem, metrics=work.metrics,
        params=LiveParams(hold_ms=300, reconnect_backoff_s=0.01, guidance_injection=False),
        transcript_observers=(work.on_transcript,), turn_observers=(work.on_turn,))
    holder["eng"] = eng
    return eng, sess, work, mem, svc, notes_sink


@aio
async def test_engine_tool_calls_respect_who_is_speaking(tmp_path):
    eng, sess, work, mem, svc, _ = engine_with_work(tmp_path)
    await eng.start()
    try:
        for ch in mic(300, "a") + hush("a", 400):                                    # Candidate A speaks
            await eng.on_meeting_audio(ch)
        sess.push(InputTranscript("Tara, write up the minutes as a pdf", True),
                  ToolCallRequested((ToolRequest("1", "create_document", {"kind": "minutes", "title": "Minutes", "format": "pdf"}),)))
        await until(lambda: sess.tool_results)
        assert sess.tool_results[0][2]["error"] == "not_authorized" and "organizer or recruiter" in json.dumps(sess.tool_results[0][2])
        for ch in mic(300, "r") + hush("r", 5000):                                    # the Recruiter speaks
            await eng.on_meeting_audio(ch)
        sess.push(InputTranscript("Tara, please write up the minutes as a pdf", True),
                  ToolCallRequested((ToolRequest("2", "create_document", {"kind": "minutes", "title": "Minutes", "format": "pdf"}),)))
        await until(lambda: len(sess.tool_results) == 2)
        assert sess.tool_results[1][2]["result"]["status"] == "started"
        job_id = sess.tool_results[1][2]["result"]["job_id"]
        await until(lambda: work.jobs.get("s1", job_id).state == "completed")
        sess.push(ToolCallRequested((ToolRequest("3", "job_status", {"job_id": job_id}),)))
        await until(lambda: len(sess.tool_results) == 3)
        assert sess.tool_results[2][2]["result"]["state"] == "completed" and sess.tool_results[2][2]["result"]["result"]["file"].endswith(".pdf")
    finally:
        await eng.close()
        await work.close()
@aio
async def test_auto_memory_and_nudges_run_from_real_turns(tmp_path):
    eng, sess, work, mem, svc, _ = engine_with_work(tmp_path)
    await eng.start()
    try:
        for ch in mic(300, "r") + hush("r", 400):
            await eng.on_meeting_audio(ch)
        sess.push(InputTranscript("We decided to use Postgres for the memory store", True), TurnComplete(1))
        await until(lambda: mem.list(SC))
        assert mem.list(SC)[0].kind == "decision" and mem.list(SC)[0].status == "candidate" and mem.list(SC)[0].body.startswith("Recruiter:")
        sess.push(InputTranscript("I always prefer short meetings in the morning", True), TurnComplete(2))
        await until(lambda: len(svc.store._items) >= 2)                 # wait for the long-term write, not just any item
        hits = await svc.recall(MemoryScope("t1", "u1"), "short meetings")
        assert hits and hits[0].item.status == "candidate" and hits[0].item.text.startswith("Recruiter said:")
        sess.push(InputTranscript("my password is hunter2 for the portal", True), TurnComplete(3))
        await asyncio.sleep(0.15)
        assert not await svc.recall(MemoryScope("t1", "u1"), "password") and all("hunter2" not in r.body for r in mem.list(SC))
        assert all("hunter2" not in json.dumps(e.data, default=str) for e in eng.bus.history)
        # a confirmed decision makes a later, related turn trigger one quiet note (never speech)
        mem.promote_latest(SC, ("decision",), confirmed_by="r")
        before = len(sess.contexts)
        sess.push(InputTranscript("let us revisit the postgres memory store design again", True), TurnComplete(4))
        await until(lambda: len(sess.contexts) > before)
        assert "confirmed a decision" in sess.contexts[-1] and "Do not interrupt" in sess.contexts[-1] and eng.playback.results == []
    finally:
        await eng.close()
        await work.close()
@aio
async def test_observer_failures_never_break_the_conversation(tmp_path):
    eng, sess, work, *_ = engine_with_work(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("observer bug")
    eng.transcript_observers = (boom,)
    eng.turn_observers = (boom, lambda t: (_ for _ in ()).throw(ValueError("x")))
    await eng.start()
    try:
        sess.push(InputTranscript("We decided to use Postgres for the memory store", True), TurnComplete(1))
        await until(lambda: eng.metrics.counters["observer_errors"] >= 2)
        assert eng.status == "active"
    finally:
        await eng.close()
        await work.close()
# ---- settings and composition root ------------------------------------------------------------------------------------------------------------
def errs(env):
    return {i.field for i in validate(RuntimeSettings.from_env(env)) if i.severity == "error"}


def test_work_settings_defaults_validation_and_redaction():
    s = RuntimeSettings.from_env({})
    assert not s.work.enabled and s.work.auto_memory == "candidate" and s.work.auto_memory_ttl_days == 30 and s.work.github_token == ""
    assert errs({"TARA_WORK_ENABLED": "1"}) == set()
    bad = {"TARA_WORK_ENABLED": "1", "TARA_AUTO_MEMORY": "everything", "TARA_AUTO_MEMORY_TTL_DAYS": "0", "TARA_GITHUB_MAX_REPOS": "99",
           "TARA_DOC_FONT_SIZE": "2", "TARA_DOC_ACCENT": "blue", "TARA_JOB_TIMEOUT_S": "1", "TARA_MAX_JOBS": "0"}
    assert errs(bad) == {"TARA_AUTO_MEMORY", "TARA_AUTO_MEMORY_TTL_DAYS", "TARA_GITHUB_MAX_REPOS", "TARA_DOC_FONT_SIZE", "TARA_DOC_ACCENT",
                         "TARA_JOB_TIMEOUT_S", "TARA_MAX_JOBS"}
    assert errs({"TARA_AUTO_MEMORY": "everything"}) == set()                               # not validated while the feature is off
    s2 = RuntimeSettings.from_env({"TARA_WORK_ENABLED": "1", "TARA_GITHUB_TOKEN": "ghp_SECRETSECRET123456"})
    assert "ghp_SECRET" not in repr(s2) and "ghp_SECRET" not in json.dumps(s2.redacted()) and s2.redacted()["work"]["github_token"] == "set"
    warn = [i for i in validate(s2) if i.severity == "warning"]
    assert any(i.field == "TARA_LLM_MODEL" for i in warn)


LIVE = {"TARA_LIVE_ENABLED": "1", "GEMINI_API_KEY": "k", "TARA_LIVE_MODEL": "live-model", "TARA_LLM_MODEL": "text-model"}


def make_app(tmp_path, extra=None, c=None, roles=None):
    env = {**LIVE, **(extra or {})}
    sess = FakeVoiceSession()
    prov = FakeVoiceProvider([sess])
    app = LiveTaraApp(RuntimeSettings.from_env(env), c or cfg(), roles or ROLES, NAMES, LocalMeetingAdapter(), session_id="x", scope=SC, provider=prov)
    return app, prov, sess


def test_app_registers_work_tools_and_instructions_only_when_enabled(tmp_path):
    off, _, _ = make_app(tmp_path)
    assert off.work is None and "work" not in off.health()
    app, prov, _ = make_app(tmp_path, {"TARA_WORK_ENABLED": "1", "TARA_WORK_DIR": str(tmp_path / "w")})
    assert app.work is not None
    decl = {d.name for d in app.engine.session_config.tools}
    assert {"create_document", "start_interview", "interview_record_answer", "recall_meeting", "meeting_recap"} <= decl
    assert "secretary" in app.engine.session_config.system_instruction and "start_interview" in app.engine.session_config.system_instruction
    assert app.health()["work"]["auto_memory"] == "candidate" and "configured" in app.health()["work"]["text_model"]
    assert app.engine.transcript_observers and app.engine.turn_observers
    run(app.close())
    nomodel, _, _ = make_app(tmp_path, {"TARA_WORK_ENABLED": "1", "TARA_LLM_MODEL": "", "TARA_WORK_DIR": str(tmp_path / "w2")})
    assert "templates only" in nomodel.health()["work"]["text_model"]
    run(nomodel.close())


@aio
async def test_app_forwards_playback_completion_to_the_interview_clock(tmp_path):
    app, _, _ = make_app(tmp_path, {"TARA_WORK_ENABLED": "1", "TARA_WORK_DIR": str(tmp_path / "w")})
    assert app.work is not None
    calls: list[tuple[int, bool, bool]] = []

    def observe(response_id: int, *, completed: bool, cancelled: bool) -> None:
        calls.append((response_id, completed, cancelled))

    app.work.on_playback_finished = observe
    app.playback.on_finished(PlaybackResult(7, 100, True, False))
    app.playback.on_finished(PlaybackResult(8, 20, False, True))
    assert calls == [(7, True, False), (8, False, True)]
    await app.close()


@aio
async def test_app_end_to_end_minutes_document(tmp_path):
    app, prov, _ = make_app(tmp_path, {"TARA_WORK_ENABLED": "1", "TARA_WORK_DIR": str(tmp_path / "w")}, roles={"r": ROLES["r"], "a": ROLES["a"]})
    await app.start()
    sess = app.engine._session
    try:
        for ch in mic(300, "r") + hush("r", 400):
            await app.engine.on_meeting_audio(ch)
        sess.push(InputTranscript("We decided to use Postgres for the memory store", True), TurnComplete(1))
        await until(lambda: app.memory.list(SC))
        sess.push(InputTranscript("Tara, make the minutes", True),
                  ToolCallRequested((ToolRequest("1", "create_document", {"kind": "minutes", "title": "Sync", "format": "md"}),)))
        await until(lambda: sess.tool_results)
        job_id = sess.tool_results[0][2]["result"]["job_id"]
        await until(lambda: app.work.jobs.get("x", job_id).state in ("completed", "failed"))
        job = app.work.jobs.get("x", job_id)
        assert job.state == "completed"
        text = Path(job.result["path"]).read_text()
        assert "Postgres" in text and "Sync" in text                          # the unconfirmed decision appears, labelled as unconfirmed
        assert "Unconfirmed" in text or "unconfirmed" in text
    finally:
        await app.close()

@aio
async def test_meeting_end_saves_minutes_and_next_meeting_sees_it(tmp_path):
    from tara.voice.memory import MemoryRecord
    store = InMemoryMemoryStore()
    svc, scope = MemoryService(store), MemoryScope("t1", "u1")
    mem = InMemoryMeetingMemory()
    mem.add(SC, MemoryRecord("decision", "Ship the beta on Friday", ("e1",)))
    mem.promote_latest(SC, confirmed_by="a")
    mem.add(SC, MemoryRecord("action_item", "Sam sends the budget", ("e2",), owner="Sam"))
    mem.promote_latest(SC, confirmed_by="a")
    mem.add(SC, MemoryRecord("decision", "Maybe drop feature X", ("e3",)))          # unconfirmed: file only
    work, _ = make_work(tmp_path, memory=mem, memory_service=svc, memory_scope=scope)
    out = await work.finalize()
    assert out["memory"] == "saved" and out["minutes_file"].endswith(".docx")
    assert (tmp_path / "work" / "documents" / out["minutes_file"]).exists()
    block = await svc.startup_context(scope)                                       # the next meeting
    assert "Ship the beta on Friday" in block and "Sam sends the budget" in block and "drop feature X" not in block


@aio
async def test_finalize_is_quiet_when_nothing_happened_or_disabled(tmp_path):
    work, _ = make_work(tmp_path)
    assert await work.finalize() == {}
    work2, _ = make_work(tmp_path, memory=InMemoryMeetingMemory())
    work2.settings = WorkSettings(enabled=True, dir=str(tmp_path / "work"), auto_minutes=False)
    assert await work2.finalize() == {}


def test_a_repeated_document_request_reuses_the_running_job_and_the_room_is_told_when_it_is_ready(tmp_path):
    async def go():
        told = []

        async def speak(text):
            told.append(text)
        work, ex = make_work(tmp_path, speak=speak)
        authorize(work)
        first = await acall(ex, "create_document", kind="minutes", title="Team sync")
        again = await acall(ex, "create_document", kind="minutes", title="team sync")
        assert first.ok and again.ok and again.output["job_id"] == first.output["job_id"]       # said twice, written once
        assert await work.jobs.drain(10) == 0
        await asyncio.sleep(0.05)
        assert told and "is ready" in told[0] and "minutes" in told[0]
        await work.close()
    asyncio.run(go())


def test_closing_gives_a_document_in_progress_a_moment_to_finish_then_cuts_it(tmp_path):
    async def go():
        from tara.core.jobs import JobBoard
        board = JobBoard()
        done = []

        async def slow():
            await asyncio.sleep(0.2)
            done.append(1)
        board.start("o", "x", slow)
        assert await board.drain(2) == 0 and done == [1]                                      # finished within the grace period
        board.start("o", "y", lambda: asyncio.sleep(30))
        assert await board.drain(0.1) == 1                                                    # too slow: reported, then close() cuts it
        await board.close()
    asyncio.run(go())


def test_jobs_cut_off_by_a_restart_are_reported_next_time_but_a_live_run_is_not_disturbed(tmp_path):
    import json
    import os

    from tara.core.jobs import JobBoard
    journal = tmp_path / "jobs-open.json"
    journal.write_text(json.dumps({"999999999": {"ab12": "document:minutes:team"}, str(os.getpid() + 0): {"zz": "document:x"}}))
    board = JobBoard(journal=journal)
    assert board.interrupted == ["document"]                       # the dead process's job is reported once...
    assert "999999999" not in json.loads(journal.read_text())      # ...and forgotten
    assert JobBoard(journal=journal).interrupted == []             # not reported twice

    async def go():
        b = JobBoard(journal=journal)
        b.start("o", "document:minutes:t", lambda: asyncio.sleep(0.05))
        assert str(os.getpid()) in json.loads(journal.read_text())  # an open job is recorded while it runs
        await b.drain(2)
        await asyncio.sleep(0.01)
        assert str(os.getpid()) not in json.loads(journal.read_text())
    asyncio.run(go())


def test_a_research_task_that_ends_reports_once_and_a_cancel_does_not():
    from tara.tools.utility.research import ResearchTaskManager

    class Svc:
        async def run(self, query, max_sources=None, max_chars=None):
            if query == "boom":
                raise RuntimeError("x")
            if query == "slow":
                await asyncio.sleep(5)
            return object()

    async def go():
        seen = []
        from tara.tools.utility.contracts import ResearchContext
        m = ResearchTaskManager(Svc(), ResearchContext("s", "t", "m"), max_tasks=3)
        m.on_done = lambda status, q, err: seen.append((status, q, err))
        await m.create("fine")
        await m.create("boom")
        slow = await m.create("slow")
        await asyncio.sleep(0.1)
        await m.cancel(slow.id)
        await asyncio.sleep(0.1)
        assert sorted(seen) == [("completed", "fine", ""), ("failed", "boom", "research_failed")]
        await m.close()
    asyncio.run(go())
