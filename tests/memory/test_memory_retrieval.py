"""Memory quality: one kind vocabulary, proposals vs decisions, corrections, ranking, recovery.

These are offline tests over the in-memory store. PostgreSQL parity is covered in
`tests/memory/test_memory_postgres.py` (opt-in, marker `db`).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from tara.memory import (
    Claim,
    InMemoryMemoryStore,
    MemoryItem,
    MemoryScope,
    MemoryService,
    classify_claim,
    durable_kind,
    meeting_slug,
    parse_query,
    recall_memory_tool,
    to_record_kind,
    utcnow,
)
from tara.memory.kinds import normalize_kind
from tara.memory.models import new_id
from tara.memory.service import span
from tara.secretary.auto_memory import AutoMemory, AutoMemoryConfig
from tara.secretary.recorder import parse_digest
from tara.secretary.tiering import Tier
from tara.voice.memory import InMemoryMeetingMemory, Scope
from tara.voice.turns import TurnRecord

run = asyncio.run


def scope(meeting: str | None = None) -> MemoryScope:
    return MemoryScope("t1", "u1", meeting_id=meeting)


def service() -> MemoryService:
    return MemoryService(InMemoryMemoryStore())


def turn(text: str, n: int = 1) -> TurnRecord:
    return TurnRecord(text, "spk", "Recruiter", 0.99, True, f"turn-{n}")


# ---- vocabulary ------------------------------------------------------------------------------------------------
def test_one_kind_vocabulary_with_explicit_translation():
    assert to_record_kind("action_item") == "action"
    assert to_record_kind("open_question") == "note"
    assert to_record_kind("proposal") == "note"
    assert normalize_kind("Action-Item") == "action"
    assert normalize_kind("todo") == "action"
    item = MemoryItem(new_id(), scope(), "action_item", "Send the deck.", "s1")
    assert item.kind == "action"
    with pytest.raises(ValueError):
        normalize_kind("banana")


def test_meeting_slug_is_still_the_scope_encoding():
    assert meeting_slug("Join Meeting #2") == "join-meeting-2"


# ---- claims ----------------------------------------------------------------------------------------------------
def test_proposal_is_not_a_decision_but_committed_wording_is():
    assert classify_claim("We should probably use PostgreSQL.") is Claim.PROPOSAL
    assert classify_claim("What if we used PostgreSQL?") is Claim.HYPOTHETICAL
    assert classify_claim("We have decided to use PostgreSQL.") is Claim.DECISION
    assert classify_claim("Actually, we switched to MySQL yesterday.") is Claim.CORRECTION
    assert durable_kind("We should probably use PostgreSQL.", "decision")[0] == "note"
    # The extractor saw context the sentence alone lacks: an assertion labelled decision is kept as a candidate.
    assert durable_kind("Beta launches on Friday.", "decision")[0] == "decision"


def test_digest_keeps_a_proposal_out_of_decisions_and_keeps_open_questions():
    transcript = ("A: We should probably use PostgreSQL for search.\n"
                  "B: We still need to decide who owns the migration.")
    raw = """{"summary": "The team discussed storage.", "facts": [
      {"text": "We should probably use PostgreSQL for search.", "kind": "decision",
       "quote": "We should probably use PostgreSQL for search."},
      {"text": "We still need to decide who owns the migration.", "kind": "open_question",
       "quote": "We still need to decide who owns the migration."}]}"""
    summary, facts = parse_digest(raw, transcript)
    assert summary == "The team discussed storage."
    proposal, question = facts
    assert proposal["kind"] == "note" and proposal["tentative"] is True
    assert proposal["claim"] == "proposal"
    assert question["kind"] == "note" and question["note_kind"] == "open_question"
    assert question["tentative"] is False


def test_digest_accepts_an_optional_correction_reference():
    transcript = "A: Correction: the launch is 19 November, not 12 November."
    raw = """{"summary": "Date fixed.", "facts": [
      {"text": "The launch is 19 November.", "kind": "fact",
       "quote": "Correction: the launch is 19 November", "corrects": "The launch is 12 November."}]}"""
    _, facts = parse_digest(raw, transcript)
    assert facts[0]["corrects"] == "The launch is 12 November."


# ---- persistence that survives a process ---------------------------------------------------------------
def test_session_note_is_persisted_and_survives_a_new_service():
    async def go():
        store = InMemoryMemoryStore()
        svc = MemoryService(store)
        live = InMemoryMeetingMemory()
        auto = AutoMemory(AutoMemoryConfig(), session_id="run-1", scope=Scope("t1", "section"),
                          meeting_memory=live, memory_service=svc,
                          memory_scope=scope("section"))
        decision = await auto.observe(turn("We decided to use Postgres for the memory store."))
        assert decision.tier is Tier.SESSION and decision.kind == "decision"
        assert live.list(Scope("t1", "section"))[0].kind == "decision"

        # Same durable store, new service object: what a restarted process sees.
        restarted = MemoryService(store)
        hits = await restarted.recall(scope("section"), "Postgres memory store")
        assert hits and hits[0].item.kind == "decision"
        assert hits[0].item.status == "candidate"
        assert hits[0].item.session_id == "run-1"
        assert hits[0].item.metadata.get("note_kind") == "decision"
    run(go())


def test_proposal_auto_note_is_stored_as_a_note_not_a_decision():
    async def go():
        store = InMemoryMemoryStore()
        auto = AutoMemory(AutoMemoryConfig(), session_id="run-1", scope=Scope("t1", "section"),
                          meeting_memory=InMemoryMeetingMemory(), memory_service=MemoryService(store),
                          memory_scope=scope("section"))
        d = await auto.observe(turn("We should probably use PostgreSQL."))
        assert d.tier is Tier.SESSION and d.kind == "proposal"
        items = [i for i in await store.list_recent(scope("section"), 10) if i.kind != "episode"]
        assert [i.kind for i in items] == ["note"]
        assert items[0].metadata.get("note_kind") == "proposal"
        assert items[0].metadata.get("tentative") is True
        assert "proposal" in str(items[0].metadata)
    run(go())


# ---- corrections ----------------------------------------------------------------------------------------------
def test_implicit_correction_links_supersedes_and_keeps_history():
    async def go():
        svc = service()
        old = await svc.remember(scope(), "The launch date for Project Atlas is 12 November.",
                                 source="s1:1", kind="decision")
        new = await svc.remember(scope(), "Correction: the launch date for Project Atlas is 19 November.",
                                 source="s1:2", kind="correction")
        assert new.supersedes_id == old.id
        assert (await svc.store.get(scope(), old.id)).status == "superseded"     # history is kept
        hits = await svc.recall(scope(), "launch date for Project Atlas")
        assert [h.item.id for h in hits] == [new.id]
        assert new.status == "confirmed"
    run(go())


def test_ambiguous_correction_is_flagged_and_neither_old_record_is_destroyed():
    async def go():
        svc = service()
        a = await svc.remember(scope(), "Project Atlas launch is planned for November.", source="s1:1",
                               kind="decision", status="candidate")
        b = await svc.remember(scope(), "Project Atlas launch is planned for December.", source="s1:2",
                               kind="decision", status="candidate")
        correction = await svc.remember(scope(), "Correction: Project Atlas launch is planned for January.",
                                        source="s1:3", kind="correction", status="candidate")
        assert correction.supersedes_id is None
        assert correction.metadata.get("possible_conflict") is True
        assert len(correction.metadata.get("correction_candidates", [])) == 2
        assert (await svc.store.get(scope(), a.id)).status == "candidate"
        assert (await svc.store.get(scope(), b.id)).status == "candidate"
    run(go())


# ---- consolidation ---------------------------------------------------------------------------------------------
def test_repeated_candidate_is_reinforced_not_duplicated():
    async def go():
        svc = service()
        first = await svc.remember(scope(), "The beta launches on Friday.", source="s1:1", status="candidate")
        again = await svc.remember(scope(), "The beta launches on Friday.", source="s1:2", status="candidate")
        assert first.id == again.id
        assert again.metadata.get("sightings") == 2
        assert len(await svc.store.list_recent(scope(), 20)) == 1
    run(go())


def test_numbered_records_are_not_treated_as_duplicates():
    async def go():
        svc = service()
        await svc.remember(scope(), "Decision 1: ship.", source="s:1", kind="decision", status="candidate")
        await svc.remember(scope(), "Decision 2: ship.", source="s:2", kind="decision", status="candidate")
        items = await svc.store.list_recent(scope(), 20)
        assert sorted(i.text for i in items) == ["Decision 1: ship.", "Decision 2: ship."]
    run(go())


def test_a_confirmed_record_is_not_relabelled_by_a_later_tentative_duplicate():
    async def go():
        svc = service()
        first = await svc.remember(scope(), "The beta launches on Friday.", source="s:1", kind="note")
        again = await svc.remember(scope(), "The beta launches on Friday.", source="s:2", kind="note",
                                   status="candidate", metadata={"tentative": True, "note_kind": "proposal"})
        assert again.id == first.id and again.status == "confirmed"
        assert "tentative" not in again.metadata
    run(go())


def test_consolidation_merges_only_near_identical_records():
    async def go():
        svc = service()
        now = utcnow()
        for i, text in enumerate(("The launch is on Friday 12 November.",
                                  "The launch is on Friday 12 November",
                                  "The launch is on Monday 15 November.")):
            await svc.store.add(MemoryItem(new_id(), scope(), "fact", text, f"s:{i}",
                                           created_at=now - timedelta(minutes=i), updated_at=now - timedelta(minutes=i)))
        merged = await svc.consolidate(scope())
        assert merged == 1
        remaining = await svc.store.list_recent(scope(), 20)
        assert len(remaining) == 2
        superseded = [i for i in svc.store._items.values() if i.status == "superseded"]
        assert len(superseded) == 1 and superseded[0].metadata.get("duplicate_of")
        assert any("Monday 15 November" in i.text for i in remaining)
    run(go())


# ---- temporal retrieval and ranking ----------------------------------------------------------------------------
def test_temporal_query_parsing_is_explicit_and_bounded():
    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    q = parse_query("what did we decide about auth last week?", now=now)
    assert q.kinds == ("decision",) and q.period_label == "the last 7 days"
    assert q.since == now - timedelta(days=7) and q.until is None
    assert "week" not in q.text.lower()
    yesterday = parse_query("yesterday", now=now)
    assert yesterday.period_label == "yesterday" and yesterday.text == ""
    assert yesterday.since is not None and yesterday.until is not None


def test_recall_applies_the_time_window_before_ranking():
    async def go():
        svc = service()
        now = utcnow()
        old = MemoryItem(new_id(), scope(), "decision", "We picked Postgres for the database.", "s:old",
                         created_at=now - timedelta(days=40), updated_at=now - timedelta(days=40))
        new = MemoryItem(new_id(), scope(), "decision", "We picked Redis for the cache.", "s:new",
                         created_at=now - timedelta(days=1), updated_at=now - timedelta(days=1))
        await svc.store.add(old)
        await svc.store.add(new)
        hits = await svc.recall(scope(), "what did we decide", since=now - timedelta(days=7))
        assert [h.item.id for h in hits] == [new.id]
    run(go())


def test_ranking_prefers_a_confirmed_record_over_a_candidate_with_equal_relevance():
    async def go():
        svc = service()
        now = utcnow() - timedelta(days=10)
        confirmed = MemoryItem(new_id(), scope(), "fact", "Billing is handled by Stripe.", "s:1",
                               status="confirmed", created_at=now, updated_at=now)
        candidate = MemoryItem(new_id(), scope(), "fact", "Billing is handled through Stripe.", "s:2",
                               status="candidate", created_at=now, updated_at=now)
        await svc.store.add(candidate)
        await svc.store.add(confirmed)
        hits = await svc.recall(scope(), "billing stripe")
        assert [h.item.id for h in hits][:2] == [confirmed.id, candidate.id]
    run(go())


# ---- startup and recovery --------------------------------------------------------------------------------------
def test_startup_context_names_the_meeting_labels_unconfirmed_and_puts_summaries_first():
    async def go():
        svc = service()
        await svc.start_session(scope("section"), "run-1")
        await svc.remember(scope("section"), "Summary of the meeting.", source="s", kind="summary",
                           session_id="run-1")
        await svc.remember(scope("section"), "We decided to use Postgres.", source="s", kind="decision",
                           session_id="run-1", status="candidate")
        await svc.remember(scope("section"), "Sam will send the report by Friday.", source="s", kind="action",
                           session_id="run-1", status="candidate",
                           metadata={"type": "action", "state": "open", "owner": "Sam"})
        block = await svc.startup_context(scope("section"), limit=8, max_chars=2000)
        assert "meeting 1" in block
        assert "unconfirmed" in block
        assert "proposal, not agreed" not in block
        assert block.index("Summary of the meeting.") < block.index("We decided to use Postgres.")
        assert block.index("We decided to use Postgres.") < block.index("Sam will send the report")
    run(go())


def test_recovery_closes_an_abandoned_meeting_and_marks_it_interrupted():
    async def go():
        svc = service()
        sc = scope("section")
        await svc.start_session(sc, "run-1")
        await svc.remember(sc, "We decided to use Postgres.", source="s", kind="decision", session_id="run-1",
                           status="candidate")
        await svc.start_session(sc, "run-2")
        recovered = await svc.recover(sc, "run-2")
        assert [m.number for m in recovered] == [1]
        sessions = await svc.sessions(sc)
        assert sessions[0].interrupted is True and sessions[0].ended_at is not None
        assert "recovered after a restart" in span(sessions[0])
        assert sessions[1].ended_at is None
        await svc.end_session(sc, "run-2")
        assert (await svc.sessions(sc))[1].interrupted is False
    run(go())


# ---- tool output ------------------------------------------------------------------------------------------------
def test_recall_tool_reports_period_labels_and_conflict_warnings():
    async def go():
        svc = service()
        sc = scope("section")
        await svc.start_session(sc, "run-1")
        await svc.remember(sc, "Billing is handled by Stripe.", source="s:1", session_id="run-1",
                           metadata={"possible_conflict": True})
        out = await recall_memory_tool(svc, sc).handler("billing stripe")
        assert out["count"] == 1
        assert out["memories"][0]["warning"].startswith("another memory may say something different")
        assert out["memories"][0]["meeting"] == "1"

        period = await recall_memory_tool(svc, sc).handler("", period="last week")
        assert period["period"] == "the last 7 days"
        assert period["count"] == 1
    run(go())


def test_recall_tool_says_plainly_when_nothing_is_stored():
    async def go():
        out = await recall_memory_tool(service(), scope()).handler("what did we decide about auth")
        assert out["count"] == 0
        assert "do not" in str(out["note"]).lower() and "remember" in str(out["note"]).lower()
    run(go())
