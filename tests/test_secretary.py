"""Short vs long term memory decisions, auto memory, minutes and meeting recall."""
import asyncio
from datetime import timedelta

import pytest

from tara.memory.models import MemoryScope, utcnow
from tara.memory.service import MemoryService
from tara.memory.store import InMemoryMemoryStore
from tara.metrics import Metrics
from tara.secretary.auto_memory import AutoMemory, AutoMemoryConfig
from tara.secretary.minutes import collect_minutes, minutes_facts
from tara.secretary.recall import RecallNudger, meeting_recap_tool, recall_meeting_tool, search_meeting
from tara.secretary.tiering import Tier, classify_turn, contains_sensitive
from tara.voice.memory import InMemoryMeetingMemory, MemoryRecord, Scope, WorkingMemory
from tara.voice.turns import TurnRecord

run = asyncio.run
SC = Scope("t1", "m1")
MS = MemoryScope("t1", "u1")


def tier(text):
    return classify_turn(text)


def test_tiering_matrix():
    cases = [
        ("I always prefer short meetings in the morning", Tier.LONG_TERM, "preference"),
        ("We use Go and PostgreSQL in our backend", Tier.LONG_TERM, "fact"),
        ("My name is Alex and I work as a platform engineer", Tier.LONG_TERM, "fact"),
        ("Actually it is Tuesday not Monday for the demo", Tier.LONG_TERM, "correction"),
        ("We decided to use Postgres for the memory store", Tier.SESSION, "decision"),
        ("Alex will send the report by Friday", Tier.SESSION, "action_item"),
        ("Can you review the migration plan before tomorrow", Tier.SESSION, "action_item"),
        ("We still need to decide who owns the rollout", Tier.SESSION, "open_question"),
        ("what time is it in Lagos", Tier.EPHEMERAL, ""),
        ("thanks", Tier.EPHEMERAL, ""),
        ("ok sure", Tier.EPHEMERAL, ""),
        ("did we decide on Postgres?", Tier.EPHEMERAL, ""),
        ("the weather looks nice today in the office", Tier.EPHEMERAL, ""),
    ]
    for text, expected_tier, kind in cases:
        d = tier(text)
        assert d.tier is expected_tier and d.kind == kind, (text, d)
    assert tier("Actually it is Tuesday not Monday for the demo").supersedes


def test_sensitive_content_is_never_kept_at_any_tier():
    for text in ("my password is hunter2 for the portal", "I prefer to use AIzaSyA1234567890abcdefghijklmnop as the key",
                 "We decided to put the token tvly-abcdefghijklmnop in the config", "my card number is 4111 1111 1111 1111",
                 "I always take my medication at nine", "we agreed the salary is high", "my religion is private to me",
                 "I work as a nurse and my diagnosis is private", "sk-abcdefghijklmnopqrstuvwx is the secret"):
        d = tier(text)
        assert d.tier is Tier.EPHEMERAL and d.reason == "sensitive", text
    assert contains_sensitive("a" * 40 + " " + "f" * 40) and not contains_sensitive("we use Go and PostgreSQL")


def turn(text, speaker="a", name="Alex", reliable=True, tid="t1"):
    return TurnRecord(text, speaker, name, 0.99 if reliable else 0.2, reliable, tid)


def mk(mode="candidate", ttl=30, promote=2, service=True):
    metrics = Metrics()
    store = InMemoryMemoryStore()
    svc = MemoryService(store, metrics=metrics) if service else None
    mem = InMemoryMeetingMemory()
    auto = AutoMemory(AutoMemoryConfig(mode, ttl, promote), session_id="s1", scope=SC, meeting_memory=mem,
                      memory_service=svc, memory_scope=MS if service else None, metrics=metrics)
    return auto, mem, svc, store, metrics


def test_session_tier_becomes_an_unconfirmed_meeting_note_with_provenance():
    auto, mem, *_ = mk()
    run(auto.observe(turn("We decided to use Postgres for the memory store")))
    run(auto.observe(turn("Alex will send the report by Friday")))
    recs = mem.list(SC)
    assert [r.kind for r in recs] == ["decision", "action_item"] and all(r.status == "candidate" for r in recs)
    assert recs[0].body == "Alex: We decided to use Postgres for the memory store" and recs[1].owner == "Alex"
    assert all(r.source_event_ids and r.source_event_ids[0].startswith("s1:auto:t1") for r in recs)


def test_long_term_candidates_expire_and_carry_the_speaker():
    auto, mem, svc, store, metrics = mk(ttl=7)
    run(auto.observe(turn("I always prefer short meetings in the morning")))
    hit = run(svc.recall(MS, "short meetings"))[0].item
    assert hit.status == "candidate" and hit.kind == "preference" and hit.text.startswith("Alex said:")
    assert hit.expires_at is not None and timedelta(days=6) < hit.expires_at - utcnow() <= timedelta(days=7)
    assert hit.confidence < 1 and hit.metadata["auto"] is True and hit.metadata["speaker"] == "Alex" and hit.source.startswith("auto:s1:")
    assert metrics.counters["auto_memory_long_term_candidates"] == 1 and mem.list(SC) == []      # not also a meeting note


def test_seeing_it_again_promotes_it_to_permanent_and_removes_the_expiry():
    auto, _, svc, store, metrics = mk(promote=2)
    for i in range(2):
        run(auto.observe(turn("I always prefer short meetings in the morning", tid=f"t{i}")))
    item = run(svc.recall(MS, "short meetings"))[0].item
    assert item.status == "confirmed" and item.expires_at is None and item.metadata["sightings"] == 2
    assert metrics.counters["auto_memory_promoted"] == 1
    auto3, _, svc3, *_ = mk(promote=3)
    for i in range(2):
        run(auto3.observe(turn("I always prefer short meetings in the morning", tid=f"t{i}")))
    again = run(svc3.recall(MS, "short meetings"))[0].item
    assert again.status == "candidate" and again.expires_at is not None and again.metadata["sightings"] == 2


def test_personal_facts_from_an_unknown_speaker_are_skipped_but_team_facts_are_kept():
    auto, mem, svc, store, metrics = mk()
    run(auto.observe(turn("I always prefer short meetings in the morning", None, None, False)))
    assert run(svc.recall(MS, "short meetings")) == [] and metrics.counters["auto_memory_skipped_unknown_speaker"] == 1
    run(auto.observe(turn("We use Go and PostgreSQL in our backend", None, None, False)))
    hit = run(svc.recall(MS, "PostgreSQL backend"))[0].item
    assert hit.text == "We use Go and PostgreSQL in our backend" and hit.metadata["speaker"] == "unknown"


def test_sensitive_small_talk_and_questions_store_nothing():
    auto, mem, svc, store, metrics = mk()
    for text in ("my password is hunter2 for the portal", "thanks", "what time is it in Lagos", "I prefer my medication schedule"):
        run(auto.observe(turn(text)))
    assert mem.list(SC) == [] and run(svc.recall(MS, "password")) == [] and run(svc.recall(MS, "medication")) == []
    assert metrics.counters["auto_memory_skipped_sensitive"] == 2 and metrics.counters["auto_memory_ephemeral"] >= 2
    assert "hunter2" not in str(dict(metrics.counters))


def test_off_mode_and_missing_services_do_nothing_and_never_raise():
    auto, mem, svc, store, metrics = mk(mode="off")
    run(auto.observe(turn("We decided to use Postgres for the memory store")))
    assert mem.list(SC) == [] and not auto.active
    no_svc, mem2, *_ = mk(service=False)
    run(no_svc.observe(turn("I always prefer short meetings in the morning")))
    assert mem2.list(SC) == []
    with pytest.raises(ValueError):
        AutoMemory(AutoMemoryConfig("everything"), session_id="s", scope=SC, meeting_memory=None)


def test_a_failing_store_is_counted_not_raised():
    class Broken(InMemoryMemoryStore):
        async def add(self, item):
            raise ConnectionError("db down")
    metrics = Metrics()
    auto = AutoMemory(AutoMemoryConfig(), session_id="s", scope=SC, meeting_memory=InMemoryMeetingMemory(),
                      memory_service=MemoryService(Broken(), metrics=metrics), memory_scope=MS, metrics=metrics)
    run(auto.observe(turn("I always prefer short meetings in the morning")))
    assert metrics.counters["auto_memory_failures"] == 1


# ---- minutes and recall ---------------------------------------------------------------------------------------------------------------
def seeded():
    mem = InMemoryMeetingMemory()
    mem.add(SC, MemoryRecord("decision", "Use Postgres for memory", ("e1",)))
    mem.add(SC, MemoryRecord("action_item", "Write the migration docs", ("e2",), owner="Alex", deadline="Friday"))
    mem.add(SC, MemoryRecord("open_question", "Who owns the rollout?", ("e3",)))
    mem.add(SC, MemoryRecord("decision", "Maybe drop Redis", ("e4",)))
    first = mem.list(SC)[0]
    mem._data[SC][0] = type(first)(first.kind, first.body, first.source_event_ids, "confirmed", confirmed_by="r")
    act = mem.list(SC)[1]
    mem._data[SC][1] = type(act)(act.kind, act.body, act.source_event_ids, "confirmed", owner=act.owner, deadline=act.deadline, confirmed_by="r")
    return mem


def test_minutes_keep_confirmed_and_unconfirmed_apart():
    w = WorkingMemory()
    for i in range(20):
        w.add_turn("Alex", f"line {i}")
    m = collect_minutes(seeded(), SC, title="Sync", attendees=["Alex", "Bea", "Alex", ""], working=w, excerpt_turns=3)
    assert [r.body for r in m.decisions] == ["Use Postgres for memory"] and m.actions[0].owner == "Alex"
    assert [r.body for r in m.unconfirmed] == ["Maybe drop Redis"] and m.attendees == ("Alex", "Bea") and len(m.excerpt) == 3
    f = minutes_facts(m)
    assert f["Confirmed decisions"] == ["Use Postgres for memory (confirmed by: r)"]
    assert "owner: Alex; deadline: Friday" in f["Confirmed action items"][0]
    assert f["Unconfirmed items (said in the meeting but never confirmed)"] == ["decision: Maybe drop Redis"]
    assert "Recent conversation (verbatim, for context only)" in f
    assert "Recent conversation (verbatim, for context only)" not in minutes_facts(m, include_excerpt=False)
    empty = collect_minutes(InMemoryMeetingMemory(), SC)
    assert empty.empty and minutes_facts(empty)["Confirmed decisions"] == ["None recorded"]


def test_recall_says_what_kind_of_evidence_each_hit_is():
    mem, w = seeded(), WorkingMemory()
    w.add_turn("Bea", "I think we should keep Redis for the cache layer")
    w.add_turn("Tara", "Redis is a fine cache")
    hits = search_meeting(mem, SC, w, "what did we say about redis")
    assert [h.source for h in hits] == ["candidate_note", "conversation"]                 # Tara's own words are never evidence
    hits2 = search_meeting(mem, SC, w, "postgres memory")
    assert hits2[0].source == "confirmed_note" and "Use Postgres" in hits2[0].text
    assert search_meeting(mem, SC, w, "kubernetes") == [] and search_meeting(mem, SC, w, "the and of") == []
    tool = recall_meeting_tool(mem, SC, w)
    assert tool.side_effect is False and tool.handler("kubernetes")["found"] is False
    found = tool.handler("redis")
    assert found["found"] and {r["source"] for r in found["results"]} == {"candidate_note", "conversation"}
    recap = meeting_recap_tool(mem, SC).handler()
    assert len(recap["confirmed"]) == 2 and len(recap["unconfirmed"]) == 2 and "mentioned but not confirmed" in recap["note"]


def test_nudger_sends_one_quiet_note_per_confirmed_item_and_never_for_candidates():
    notes = []
    n = RecallNudger(seeded(), SC, notes.append, min_overlap=2, max_nudges=2)
    assert n.observe("should we use redis or something else") is None                   # only a candidate matches: no nudge
    note = n.observe("can we talk about the postgres memory design again")
    assert note and "confirmed a decision" in note and "Do not interrupt" in note and notes == [note]
    assert n.observe("postgres memory again please") is None                              # once per item
    n2 = RecallNudger(seeded(), SC, notes.append, min_overlap=2, max_nudges=1)
    assert n2.observe("write the migration docs") and n2.observe("postgres memory design") is None   # bounded count
