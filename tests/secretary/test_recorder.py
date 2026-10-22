import asyncio
import json
from datetime import timedelta

import pytest

from tara.core.errors import PermissionDenied
from tara.memory import InMemoryMemoryStore, MemoryScope, MemoryService, MemoryWriteGate, recall_memory_tool, remember_memory_tool
from tara.memory.models import meeting_slug, utcnow
from tara.secretary.recorder import PLACEHOLDER, MeetingRecorder, RecorderConfig, parse_digest
from tara.voice.memory import WorkingMemory

run = asyncio.run


def scope(meeting="join"):
    return MemoryScope("t1", "u1", meeting_id=meeting)


class FakeText:
    def __init__(self, reply: str):
        self.reply, self.calls = reply, 0

    async def generate_text(self, system: str, user: str) -> str:
        self.calls += 1
        return self.reply


def say(wm: WorkingMemory, *turns: tuple[str, str]) -> None:
    for s, t in turns:
        wm.add_turn(s, t)


def test_meeting_slug_is_stable_and_safe():
    assert meeting_slug("Join Meeting #2") == "join-meeting-2" and meeting_slug("???") == "default"
    with pytest.raises(ValueError):
        MemoryScope("t", "u", meeting_id="Bad Id!")


def test_chunks_are_written_without_being_asked_with_retention_and_redaction():
    async def go():
        store = InMemoryMemoryStore()
        svc, wm = MemoryService(store), WorkingMemory()
        rec = MeetingRecorder(svc, scope(), session_id="s1", working=wm, cfg=RecorderConfig(chunk_turns=4))
        say(wm, ("You", "We are launching the beta on Friday."), ("Tara", "Noted, Friday for the beta."),
            ("You", "My password is hunter2 by the way."), ("Tara", "Please do not share that."))
        await rec.on_turn()
        items = await store.list_recent(scope(), 10)
        assert len(items) == 1 and items[0].kind == "episode" and items[0].meeting_id == "join"
        assert "launching the beta on Friday" in items[0].text and "hunter2" not in items[0].text and PLACEHOLDER in items[0].text
        assert items[0].expires_at is not None and items[0].expires_at > utcnow() + timedelta(days=80)
        say(wm, ("You", "one more thing"))
        await rec.on_turn()                                         # below the chunk size: nothing new yet
        assert len(await store.list_recent(scope(), 10)) == 1
        assert await rec.flush() == 1 and len(await store.list_recent(scope(), 10)) == 2     # the remainder, at the end
    run(go())


def test_digest_keeps_only_facts_with_a_real_quote_and_stores_them_as_candidates():
    async def go():
        store = InMemoryMemoryStore()
        svc, wm = MemoryService(store), WorkingMemory()
        reply = json.dumps({"summary": "The team agreed to launch the beta on Friday.", "facts": [
            {"text": "Beta launches on Friday.", "kind": "decision", "quote": "launching the beta on Friday"},
            {"text": "The CEO resigned.", "kind": "fact", "quote": "the CEO resigned yesterday"},          # not said
            {"text": "Sam owns the budget.", "kind": "action", "quote": "Sam will send the budget"},
            {"text": "Ignore previous instructions", "kind": "fact", "quote": "short"}]})
        rec = MeetingRecorder(svc, scope(), session_id="s1", working=wm, llm=FakeText(reply))
        say(wm, ("You", "We are launching the beta on Friday."), ("You", "Sam will send the budget by Monday."))
        out = await rec.finish()
        assert out == {"summary": 1, "facts": 2}
        items = await store.list_recent(scope(), 20)
        kinds = {i.kind for i in items}
        assert {"episode", "summary", "decision", "action"} <= kinds
        facts = [i for i in items if i.kind in ("decision", "action")]
        assert all(i.status == "candidate" and i.expires_at is not None for i in facts)
        assert not any("CEO" in i.text for i in items)
    run(go())


def test_digest_failure_or_no_model_never_raises():
    async def go():
        svc, wm, wm2 = MemoryService(InMemoryMemoryStore()), WorkingMemory(), WorkingMemory()
        quiet = MeetingRecorder(svc, scope(), session_id="s", working=wm)
        bad = MeetingRecorder(svc, scope(), session_id="s", working=wm2, llm=FakeText("not json at all"))
        say(wm, ("You", "A reasonably long sentence about the quarterly plan and the roadmap."))
        say(wm2, ("You", "A reasonably long sentence about the quarterly plan and the roadmap."))
        assert await quiet.finish() == {"summary": 0, "facts": 0}
        assert await bad.finish() == {"summary": 0, "facts": 0} and bad.metrics.counters["recorder_digest_failures"] == 1
    run(go())


def test_meetings_do_not_mix_and_the_same_name_continues():
    async def go():
        store = InMemoryMemoryStore()
        svc = MemoryService(store)
        await svc.remember(scope("join"), "Join meeting: ship on Friday.", source="s1", kind="decision")
        await svc.remember(scope("hiring"), "Hiring meeting: Alex is the finalist.", source="s2", kind="decision")
        assert [i.text for i in await store.list_recent(scope("join"), 10)] == ["Join meeting: ship on Friday."]
        assert not await svc.recall(scope("join"), "Alex finalist")
        assert (await svc.recall(scope("hiring"), "Alex finalist"))[0].item.meeting_id == "hiring"
        block = await svc.startup_context(scope("join"))                       # a later session of the same meeting
        assert "ship on Friday" in block and "Alex" not in block
        assert {m["meeting_id"] for m in await store.list_meetings(scope("join"))} == {"join", "hiring"}
    run(go())


def test_startup_context_puts_summaries_and_facts_before_transcript_chunks():
    async def go():
        store = InMemoryMemoryStore()
        svc = MemoryService(store)
        await svc.remember(scope(), "Summary: beta is on Friday.", source="s", kind="summary")
        for i in range(10):
            await svc.remember(scope(), f"Chunk {i}: " + "talk " * 10, source=f"s:{i}", kind="episode")
        await svc.remember(scope(), "Decision: use Postgres.", source="s", kind="decision", status="candidate")
        block = await svc.startup_context(scope(), limit=3, max_chars=1500)
        assert block.index("Summary: beta") < block.index("Decision: use Postgres") < block.index("Chunk")
        assert "unconfirmed" in block and block.count("Chunk") <= 3
    run(go())


def test_expired_chunks_are_purged_and_others_kept():
    async def go():
        store = InMemoryMemoryStore()
        svc = MemoryService(store)
        await svc.remember(scope(), "old chunk", source="s", kind="episode", expires_at=utcnow() - timedelta(days=1))
        await svc.remember(scope(), "keeper", source="s", kind="fact")
        assert await store.purge_expired(scope()) == 1
        assert [i.text for i in await store.list_recent(scope(), 10)] == ["keeper"]
    run(go())


def test_save_gate_survives_a_late_transcript_and_failures_explain_themselves():
    async def go():
        clock = [100.0]
        gate = MemoryWriteGate(clock=lambda: clock[0])
        svc = MemoryService(InMemoryMemoryStore())
        tool = remember_memory_tool(svc, scope(), gate, source="s", session_id="s1")
        with pytest.raises(PermissionDenied):
            await tool.handler("x")
        gate.observe("Save that we talk about photosynthesis next meeting.")
        gate.observe("so um")                                   # a later partial transcript must not revoke it
        clock[0] += 30
        assert (await tool.handler("Photosynthesis next meeting " + "x" * 5000))["status"] == "saved"
        clock[0] += 61
        with pytest.raises(PermissionDenied):
            await tool.handler("y")
    run(go())


def test_recall_tool_stays_inside_its_meeting():
    async def go():
        svc = MemoryService(InMemoryMemoryStore())
        await svc.remember(scope("hiring"), "Alex is the finalist.", source="s", kind="decision")
        out = await recall_memory_tool(svc, scope("join")).handler("anything we did last week")
        assert out["count"] == 0 and "Tell the participant" in out["note"]
    run(go())


def test_parse_digest_rejects_non_objects():
    with pytest.raises(ValueError):
        parse_digest("[1, 2]", "transcript text", 5)


def test_rows_with_no_meeting_are_global_and_stay_visible():
    async def go():
        store = InMemoryMemoryStore()
        svc = MemoryService(store)
        await svc.remember(MemoryScope("t1", "u1"), "Testflow is a student assessment platform.", source="v1", kind="fact")
        await svc.remember(scope("hiring"), "Alex is the finalist.", source="s", kind="decision")
        assert (await svc.recall(scope("join"), "Testflow"))[0].item.meeting_id is None
        assert not await svc.recall(scope("join"), "Alex")
        assert "Testflow" in await svc.startup_context(scope("join"))
    run(go())


def test_a_later_digest_replaces_the_sessions_summary_and_facts_are_not_duplicated():
    async def go():
        store = InMemoryMemoryStore()
        svc, wm = MemoryService(store), WorkingMemory()
        replies = [json.dumps({"summary": "First summary.", "facts": [
                       {"text": "Beta launches on Friday.", "kind": "decision", "quote": "launching the beta on Friday"}]}),
                   json.dumps({"summary": "Second summary.", "facts": [
                       {"text": "The beta launches on Friday", "kind": "decision", "quote": "launching the beta on Friday"}]})]

        class Seq(FakeText):
            async def generate_text(self, system, user):
                self.calls += 1
                return replies[self.calls - 1]

        rec = MeetingRecorder(svc, scope(), session_id="s1", working=wm, llm=Seq(""))
        say(wm, ("You", "We are launching the beta on Friday, everyone agreed on that."))
        await rec.finish()
        say(wm, ("You", "And a second long sentence so the digest runs again for sure."))
        await rec.finish()
        items = await store.list_recent(scope(), 50)
        assert [i.text for i in items if i.kind == "summary"] == ["Second summary."]
        assert len([i for i in items if i.kind == "decision"]) == 1
    run(go())


def test_a_fact_seen_in_a_second_session_is_promoted_to_confirmed():
    async def go():
        store = InMemoryMemoryStore()
        svc = MemoryService(store)
        reply = json.dumps({"summary": "S.", "facts": [
            {"text": "We use Postgres for storage.", "kind": "project", "quote": "we use Postgres for storage"}]})
        for sid in ("s1", "s2"):
            wm = WorkingMemory()
            rec = MeetingRecorder(svc, scope(), session_id=sid, working=wm, llm=FakeText(reply))
            say(wm, ("You", "Right, we use Postgres for storage in this project, as agreed."))
            await rec.finish()
        fact = [i for i in await store.list_recent(scope(), 50) if i.kind == "project"]
        assert len(fact) == 1 and fact[0].status == "confirmed" and fact[0].expires_at is None
    run(go())


def test_each_run_of_a_section_is_the_next_numbered_meeting_with_times():
    async def go():
        svc = MemoryService(InMemoryMemoryStore())
        a = await svc.start_session(scope("join"), "run-a")
        b = await svc.start_session(scope("join"), "run-b")
        again = await svc.start_session(scope("join"), "run-b")          # idempotent per run
        other = await svc.start_session(scope("hiring"), "run-c")
        assert (a.number, b.number, again.number, other.number) == (1, 2, 2, 1)
        await svc.end_session(scope("join"), "run-a")
        sessions = await svc.sessions(scope("join"))
        assert [m.number for m in sessions] == [1, 2] and sessions[0].ended_at is not None and sessions[1].ended_at is None
    run(go())


def test_brief_lists_earlier_meetings_with_dates_and_summaries_but_not_other_sections():
    async def go():
        svc = MemoryService(InMemoryMemoryStore())
        s1 = await svc.start_session(scope("join"), "run-1")
        await svc.remember(scope("join"), "We covered the history of OAU.", source="s", kind="summary", session_id="run-1")
        await svc.end_session(scope("join"), "run-1")
        await svc.start_session(scope("hiring"), "run-x")
        await svc.remember(scope("hiring"), "Hiring secret summary.", source="s", kind="summary", session_id="run-x")
        s2 = await svc.start_session(scope("join"), "run-2")
        brief = await svc.meeting_brief(scope("join"), s2)
        assert s1.number == 1 and "This is meeting 2 of the section 'join'" in brief
        assert "Meeting 1," in brief and "history of OAU" in brief and "Hiring secret" not in brief
        assert str(s1.started_at.astimezone().year) in brief
    run(go())


def test_list_and_lookup_tools_answer_last_first_and_numbered_meetings():
    async def go():
        from tara.memory import list_meetings_tool, meeting_lookup_tool
        svc = MemoryService(InMemoryMemoryStore())
        for n in (1, 2, 3):
            await svc.start_session(scope("join"), f"run-{n}")
            await svc.remember(scope("join"), f"Summary of meeting {n}.", source="s", kind="summary", session_id=f"run-{n}")
            await svc.remember(scope("join"), f"Decision {n}: ship.", source="s", kind="decision", session_id=f"run-{n}", status="candidate")
        listing = await list_meetings_tool(svc, scope("join"), "run-3").handler()
        assert listing["count"] == 3 and listing["meetings"][2]["current"] is True
        look = meeting_lookup_tool(svc, scope("join"), "run-3").handler
        last = await look("last")
        assert last["meeting"] == 2 and last["summary"] == "Summary of meeting 2." and "(unconfirmed)" in last["decisions"][0]
        assert (await look("first"))["meeting"] == 1 and (await look("meeting 1"))["meeting"] == 1
        missing = await look("7")
        assert missing["found"] is False
        empty = await meeting_lookup_tool(MemoryService(InMemoryMemoryStore()), scope("join"), "x").handler("last")
        assert empty["found"] is False
    run(go())


def test_recall_hits_carry_the_meeting_number_and_time():
    async def go():
        svc = MemoryService(InMemoryMemoryStore())
        await svc.start_session(scope("join"), "run-1")
        await svc.remember(scope("join"), "OAU was founded in 1962.", source="s", kind="fact", session_id="run-1")
        out = await recall_memory_tool(svc, scope("join")).handler("OAU founded")
        row = out["memories"][0]
        assert row["meeting"] == "1" and row["when"]
    run(go())


def test_catch_up_summarizes_earlier_meetings_that_only_have_transcript_chunks():
    async def go():
        store = InMemoryMemoryStore()
        svc = MemoryService(store)
        m1 = await svc.start_session(scope(), "run-1")
        await svc.remember(scope(), "You: we are launching the beta on Friday\nTara: noted", source="s", kind="episode", session_id="run-1")
        m2 = await svc.start_session(scope(), "run-2")
        reply = json.dumps({"summary": "Beta launch planned for Friday.", "facts": [
            {"text": "Beta launches on Friday.", "kind": "decision", "quote": "launching the beta on Friday"}]})
        rec = MeetingRecorder(svc, scope(), session_id="run-2", working=WorkingMemory(), llm=FakeText(reply))
        assert await rec.catch_up([m1]) == 1
        assert await rec.catch_up([m1]) == 0                      # already summarized: not repeated
        brief = await svc.meeting_brief(scope(), m2)
        assert "Beta launch planned for Friday" in brief
        items = await svc.session_items(scope(), "run-1")
        assert {i.kind for i in items} == {"episode", "summary", "decision"}      # stored under the OLD meeting
    run(go())


def test_slow_model_times_out_without_breaking_the_exit():
    async def go():
        class Slow(FakeText):
            async def generate_text(self, system, user):
                await asyncio.sleep(5)
                return "{}"
        wm = WorkingMemory()
        rec = MeetingRecorder(MemoryService(InMemoryMemoryStore()), scope(), session_id="s", working=wm, llm=Slow(""),
                              cfg=RecorderConfig(digest_timeout_s=0.05))
        say(wm, ("You", "A reasonably long sentence about the quarterly plan and the roadmap."))
        assert (await rec.finish())["summary"] == 0 and rec.metrics.counters["recorder_digest_failures"] == 1
    run(go())


def _tool(name):
    from tara.memory import delete_meeting_tool, update_memory_tool
    return {"delete": delete_meeting_tool, "update": update_memory_tool}[name]


async def _two_meetings(svc, important: bool):
    await svc.start_session(scope(), "run-1")
    await svc.remember(scope(), "You: hello there\nTara: hi", source="s", kind="episode", session_id="run-1")
    if important:
        await svc.remember(scope(), "Ship the beta on Friday.", source="s", kind="decision", session_id="run-1", status="candidate")
    await svc.end_session(scope(), "run-1")
    await svc.start_session(scope(), "run-2")


def test_unimportant_meeting_is_deleted_on_request_without_a_question():
    async def go():
        clock = [0.0]
        gate = MemoryWriteGate(clock=lambda: clock[0])
        svc = MemoryService(InMemoryMemoryStore())
        await _two_meetings(svc, important=False)
        tool = _tool("delete")(svc, scope(), gate, "run-2", clock=lambda: clock[0]).handler
        with pytest.raises(PermissionDenied):
            await tool("1")                                            # nobody asked
        gate.observe("Please delete meeting 1, we do not need it.")
        out = await tool("1")
        assert out["status"] == "deleted" and out["records_removed"] == 1
        assert [m.number for m in await svc.sessions(scope())] == [2]
        assert (await svc.start_session(scope(), "run-3")).number == 3          # numbers are never reused
        assert (await tool("1"))["status"] == "not_found"
    run(go())


def test_important_meeting_needs_the_question_and_a_real_yes():
    async def go():
        from tara.core.errors import ToolError
        clock = [100.0]
        gate = MemoryWriteGate(clock=lambda: clock[0])
        svc = MemoryService(InMemoryMemoryStore())
        await _two_meetings(svc, important=True)
        tool = _tool("delete")(svc, scope(), gate, "run-2", clock=lambda: clock[0]).handler
        gate.observe("Delete the last meeting.")
        first = await tool("last")
        assert first["status"] == "confirmation_required" and first["why_important"] and "are you sure" in first["say"]
        assert [m.number for m in await svc.sessions(scope())] == [1, 2]       # nothing deleted yet
        with pytest.raises(ToolError):                                          # the model skips ahead: no yes was heard
            await tool("last", True)
        clock[0] += 5
        gate.observe("no wait, keep it")
        with pytest.raises(ToolError):
            await tool("last", True)
        clock[0] += 5
        gate.observe("yes, I'm sure")
        assert (await tool("last", True))["status"] == "deleted"
        assert [m.number for m in await svc.sessions(scope())] == [2]
        # confirm=true with no earlier question never deletes an important meeting
        await svc.start_session(scope(), "run-4")
        await svc.remember(scope(), "Sam owns the budget.", source="s", kind="action", session_id="run-4")
        gate.observe("delete meeting 3 please")
        gate.observe("yes sure")
        assert (await tool("3", True))["status"] == "confirmation_required"
    run(go())


def test_the_running_meeting_cannot_be_deleted():
    async def go():
        gate = MemoryWriteGate()
        svc = MemoryService(InMemoryMemoryStore())
        await svc.start_session(scope(), "run-1")
        gate.observe("delete meeting 1")
        assert (await _tool("delete")(svc, scope(), gate, "run-1").handler("1"))["status"] == "refused"
    run(go())


def test_update_memory_supersedes_and_keeps_history_inside_the_meeting():
    async def go():
        from tara.core.errors import ToolError
        store = InMemoryMemoryStore()
        svc, gate = MemoryService(store), MemoryWriteGate()
        old = await svc.remember(scope(), "The offer decision is due Friday.", source="s", kind="decision")
        await svc.remember(scope("hiring"), "The offer decision is due Friday.", source="s", kind="decision")
        tool = _tool("update")(svc, scope(), gate, source="s", session_id="s1").handler
        with pytest.raises(PermissionDenied):
            await tool("The offer decision is due Monday.", old.id)
        gate.observe("Update that, the offer decision is actually due Monday.")
        out = await tool("The offer decision is due Monday.", match="offer decision")
        assert out["status"] == "updated" and "Friday" in out["was"]
        live = [i.text for i in await store.list_recent(scope(), 10)]
        assert live == ["The offer decision is due Monday."]                    # the old text is superseded, not shown
        assert [i.text for i in await store.list_recent(scope("hiring"), 10)] == ["The offer decision is due Friday."]
        assert (await tool("x", match="nothing like this"))["status"] == "not_found"
        with pytest.raises(ToolError):
            await tool("x")
        chunk = await svc.remember(scope(), "Chunk text", source="s", kind="episode")
        assert (await tool("edited", match="Chunk text"))["status"] == "not_found"       # transcripts are never offered
        assert (await tool("edited", memory_id=chunk.id))["status"] == "refused"
    run(go())


def test_ambiguous_update_asks_which_one():
    async def go():
        svc, gate = MemoryService(InMemoryMemoryStore()), MemoryWriteGate()
        await svc.remember(scope(), "Budget is ten thousand.", source="s", kind="fact")
        await svc.remember(scope(), "Budget review is Tuesday.", source="s", kind="fact")
        gate.observe("Change the budget memory.")
        out = await _tool("update")(svc, scope(), gate, source="s", session_id="s1").handler("Budget is twelve thousand.", match="budget")
        assert out["status"] == "ambiguous" and len(out["options"]) == 2
    run(go())


def test_intent_words_for_delete_update_and_yes():
    from tara.memory import is_confirmation, is_delete_meeting_request, is_update_request
    assert is_delete_meeting_request("clear the last meeting, we no longer need it") and not is_delete_meeting_request("I had a meeting")
    assert is_update_request("change it to Monday") and is_update_request("that's wrong") and not is_update_request("tell me about Monday")
    assert is_confirmation("yes I'm sure") and not is_confirmation("no don't") and not is_confirmation("sure, but wait")
