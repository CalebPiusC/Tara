"""Notes recorder: candidates with provenance, explicit promotion, and a control-role gate.

All of this is offline. It proves the application rules, not how the Live model behaves.
"""
import asyncio

import pytest
from helpers import ROLES, TH, cfg

from tara.adapters.local import LocalMeetingAdapter
from tara.audio import silence, tone
from tara.classify import RuleIntentClassifier
from tara.config import ToolSpec
from tara.contracts import PCMChunk
from tara.control.state import ControlState
from tara.metrics import Metrics
from tara.tools import ToolExecutor
from tara.voice.engine import LiveParams, LiveVoiceEngine
from tara.voice.events import InputTranscript, ToolCallRequested, ToolRequest, VoiceSessionConfig
from tara.voice.fakes import FakeVoiceProvider, FakeVoiceSession
from tara.voice.memory import InMemoryMeetingMemory, MemoryRecord, Scope, record_note_tool, summarise_agreed
from tara.voice.participation import LiveParticipation
from tara.voice.registry import SessionRegistry

run = asyncio.run
NAMES = {"a": "Candidate A", "r": "Recruiter"}


async def until(cond, timeout=3.0):
    end = asyncio.get_event_loop().time() + timeout
    while not cond():
        if asyncio.get_event_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


def mic(ms, stream):
    pcm = tone(ms)
    return [PCMChunk(pcm[i:i + 640], 16000, i // 32, stream) for i in range(0, len(pcm), 640)]


def hush(stream, t0=3000):
    return [PCMChunk(silence(500), 16000, t0, stream)]


async def speak_as(eng, stream, ms=300):
    for ch in mic(ms, stream) + hush(stream, 4000):
        await eng.on_meeting_audio(ch)


def mk_notes():
    c = cfg()
    scope = Scope("t", "m")
    memory = InMemoryMeetingMemory()
    control = ControlState()
    executor = ToolExecutor({}, ())
    executor.register_and_grant(
        record_note_tool(memory, scope, lambda: control.notes_active, source="s1"),
        ToolSpec("record_note", "notes", requires_human_approval=False))
    ad = LocalMeetingAdapter(realtime_factor=0.1, controls=True)
    s = FakeVoiceSession()
    eng = LiveVoiceEngine(
        session_id="s1", scope=scope, cfg=c, roles=ROLES, names=NAMES, provider=FakeVoiceProvider([s]),
        session_config=VoiceSessionConfig("sys"), adapter=ad,
        participation=LiveParticipation(c, ROLES, RuleIntentClassifier(), TH, caps=ad.capabilities, identity_available=True),
        registry=SessionRegistry(2), metrics=Metrics(), tools=executor, meeting_memory=memory, control=control,
        params=LiveParams(hold_ms=300, floor_wait_ms=300, leave_grace_s=0.3, guidance_injection=False))
    return eng, s, memory, scope


def tool_call(call_id="c1", **args):
    return ToolCallRequested((ToolRequest(call_id, "record_note", args),))


async def notes_on(eng, s):
    await speak_as(eng, "r")
    s.push(InputTranscript("Tara, take notes on this discussion", True))
    await until(lambda: eng.control.notes_active)


def test_record_note_is_refused_until_notes_mode_is_on_then_writes_a_candidate():
    async def go():
        eng, s, memory, scope = mk_notes()
        await eng.start()
        try:
            s.push(tool_call("c0", kind="decision", body="ship on friday"))
            await until(lambda: len(s.tool_results) == 1)
            assert "PermissionDenied" in s.tool_results[0][2]["error"]
            assert memory.list(scope) == []                         # nothing is recorded without the spoken mode

            await notes_on(eng, s)
            s.push(tool_call("c1", kind="decision", body="ship on friday", owner="recruiter", deadline="friday"))
            await until(lambda: len(s.tool_results) == 2)
            assert s.tool_results[1][2]["result"] == {"status": "candidate", "kind": "decision"}
            records = memory.list(scope)
            assert len(records) == 1 and records[0].status == "candidate"
            assert records[0].body == "ship on friday" and records[0].owner == "recruiter"
            assert records[0].deadline == "friday" and records[0].source_event_ids
        finally:
            await eng.close()
    run(go())


def test_record_note_validates_kind_and_body():
    async def go():
        eng, s, memory, scope = mk_notes()
        await eng.start()
        try:
            await notes_on(eng, s)
            s.push(tool_call("c1", kind="gossip", body="something"))
            await until(lambda: len(s.tool_results) == 1)
            assert "ValueError" in s.tool_results[0][2]["error"]
            s.push(tool_call("c2", kind="decision", body="   "))
            await until(lambda: len(s.tool_results) == 2)
            assert "ValueError" in s.tool_results[1][2]["error"]
            assert memory.list(scope) == []
        finally:
            await eng.close()
    run(go())


def test_only_a_control_role_can_confirm_and_promotion_is_explicit():
    async def go():
        eng, s, memory, scope = mk_notes()
        await eng.start()
        try:
            await notes_on(eng, s)
            s.push(tool_call(kind="decision", body="ship on friday"))
            await until(lambda: len(memory.list(scope)) == 1)

            await speak_as(eng, "a")
            s.push(InputTranscript("Tara, confirm that", True))
            await until(lambda: eng.metrics.counters["control_denied.confirm_note.needs_control_role"] == 1)
            assert memory.list(scope)[0].status == "candidate"      # a candidate cannot confirm

            await speak_as(eng, "r")
            s.push(InputTranscript("Tara, confirm that", True))
            await until(lambda: eng.metrics.counters["control_note_confirmed"] == 1)
            rec = memory.list(scope)[0]
            assert rec.status == "confirmed" and rec.confirmed_by == "r"
            with pytest.raises(ValueError):
                memory.add(scope, MemoryRecord("decision", "sneaky", ("e9",), status="confirmed"))
        finally:
            await eng.close()
    run(go())


def test_summary_separates_confirmed_from_unconfirmed_and_preserves_provenance():
    memory = InMemoryMeetingMemory()
    scope = Scope("t", "m")
    memory.add(scope, MemoryRecord("decision", "ship friday", ("e1",)))
    memory.add(scope, MemoryRecord("action_item", "write docs", ("e2",), owner="r"))
    promoted = memory.promote_latest(scope, ("decision", "action_item"), confirmed_by="r")
    assert promoted is not None and promoted.status == "confirmed" and promoted.confirmed_by == "r"
    assert promoted.source_event_ids == ("e2",) and promoted.owner == "r"
    memory.add(scope, MemoryRecord("open_question", "who owns rollout?", ("e3",)))

    confirmed, unconfirmed = summarise_agreed(memory, scope)
    assert [r.body for r in confirmed] == ["write docs"]
    assert [r.body for r in unconfirmed] == ["ship friday", "who owns rollout?"]
    assert memory.promote_latest(scope, ("summary",)) is None       # no invented promotion


def test_confirm_with_no_candidate_says_so_without_inventing_one():
    async def go():
        eng, s, memory, scope = mk_notes()
        await eng.start()
        try:
            await speak_as(eng, "r")
            s.push(InputTranscript("Tara, confirm that", True))
            await until(lambda: eng.metrics.counters["control_note_confirm_empty"] == 1)
            assert memory.list(scope) == [] and s.texts                # prompt sent, no record created
        finally:
            await eng.close()
    run(go())
