import asyncio

import pytest
from helpers import ROLES, TH, cfg, group_cfg

from tara.adapters.local import LocalMeetingAdapter
from tara.classify import RuleIntentClassifier
from tara.config import Mode, SpeakingPolicy, ToolSpec
from tara.contracts import Passage, PCMChunk
from tara.errors import DuplicateSession, SessionLimitExceeded
from tara.knowledge import InMemoryKnowledge
from tara.metrics import Metrics
from tara.participation import Attribution
from tara.tools import ToolDef, ToolExecutor
from tara.voice.app import knowledge_tool, tool_declarations
from tara.voice.memory import InMemoryMeetingMemory, MemoryRecord, Scope, WorkingMemory, scoped_search
from tara.voice.participation import LiveParticipation, Verdict
from tara.voice.playback import PlaybackController
from tara.voice.registry import SessionRegistry

run = asyncio.run


def chunk(ms=100, rate=24000):
    return PCMChunk(b"\x00\x00" * (rate * ms // 1000), rate, 0, "tara")


async def until(cond, timeout=2.0):
    end = asyncio.get_event_loop().time() + timeout
    while not cond():
        if asyncio.get_event_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


# ---- playback -----------------------------------------------------------------------------------------
def pc(factor=1.0, **kw):
    ad = LocalMeetingAdapter(realtime_factor=factor)
    return PlaybackController(ad, Metrics(), **kw), ad


def test_plays_to_completion_and_records_result():
    async def go():
        p, ad = pc(0.1)
        for _ in range(5):
            assert p.enqueue(1, chunk())
        p.finish(1)
        await until(lambda: p.results)
        r = p.results[0]
        assert r.completed and not r.cancelled and r.delivered_ms == 500 and ad.completed["live_1"] is True
        assert p.active_response is None
    run(go())


def test_cancel_stops_audio_now_and_rejects_stale_chunks_then_next_response_plays():
    async def go():
        p, ad = pc(1.0)
        for _ in range(10):
            p.enqueue(1, chunk())
        await asyncio.sleep(0.25)
        assert p.is_playing
        delivered = await p.cancel("test")
        assert delivered is not None and delivered < 1000 and not p.is_playing
        assert ad.completed["live_1"] is False
        assert p.enqueue(1, chunk()) is False                   # same dead response: stale
        assert p.metrics.counters["stale_chunks_dropped"] == 1
        assert p.metrics.summary("live_interrupt_stop_ms")["count"] == 1
        assert p.metrics.counters["playback_cancelled.test"] == 1
        for _ in range(2):
            assert p.enqueue(2, chunk())                         # a new response is unaffected
        p.finish(2)
        await until(lambda: len(p.results) == 2)
        assert p.results[1].completed and p.results[1].response_id == 2
    run(go())


def test_cancel_when_idle_is_a_noop_and_can_pre_kill_future_ids():
    async def go():
        p, _ = pc()
        assert await p.cancel("idle") is None
        assert await p.cancel("idle", up_to=3) is None
        assert p.enqueue(3, chunk()) is False and p.enqueue(4, chunk()) is True and p.is_stale(2)
        await p.close()
    run(go())


def test_queue_is_bounded_and_drops_oldest():
    async def go():
        ad = LocalMeetingAdapter(realtime_factor=1.0)
        p = PlaybackController(ad, Metrics(), max_queue_chunks=3)
        for i in range(10):
            p.enqueue(1, PCMChunk(b"\x00\x00" * 2400, 24000, i, "tara"))
        assert p.metrics.counters["playback_chunks_dropped"] >= 5
        assert p._active.q.qsize() <= 3
        await p.cancel("x")
    run(go())


def test_adapter_failures_do_not_crash_and_never_report_delivery():
    async def go():
        class Broken(LocalMeetingAdapter):
            async def send_audio(self, uid, chunks):
                raise OSError("sink down")
        p = PlaybackController(Broken(), Metrics())
        p.enqueue(1, chunk())
        await until(lambda: p.results)
        assert p.results[0].completed is False and p.metrics.counters["playback_errors"] == 1

        class HangStop(LocalMeetingAdapter):
            async def stop_audio(self, uid):
                await asyncio.sleep(10)
                return 0
        p2 = PlaybackController(HangStop(realtime_factor=1.0), Metrics(), stop_timeout_s=0.1)
        for _ in range(5):
            p2.enqueue(1, chunk())
        await asyncio.sleep(0.15)
        t0 = asyncio.get_event_loop().time()
        await p2.cancel("x")
        assert asyncio.get_event_loop().time() - t0 < 1.0 and p2.metrics.counters["playback_stop_timeouts"] >= 1
    run(go())


# ---- live participation -----------------------------------------------------------------------------------------
def lp(c):
    return LiveParticipation(c, ROLES, RuleIntentClassifier(), TH)


A = Attribution(None, 0.0)
R = Attribution("r", 0.99)


def ev(c, text, att=A, **kw):
    return run(lp(c).evaluate(text, att, **kw))


def test_participation_matrix():
    c = cfg()
    assert ev(c, "Tara, can you explain the deployment?").verdict is Verdict.ALLOW
    assert ev(c, "we should ship on friday").reason == "not_addressed"
    assert ev(c, "I asked Tara about that yesterday").reason == "not_addressed"
    assert ev(c, "", ).reason == "no_transcript"
    assert ev(c, "Tara, hello", muted=True).reason == "muted"
    assert ev(c, "Tara, hello", takeover_active=True).reason == "takeover"
    assert ev(cfg(mode=Mode.OBSERVE, policy=SpeakingPolicy.SILENT), "Tara, hello").reason == "policy_silent"
    assert ev(cfg(policy=SpeakingPolicy.PROACTIVE_ALLOWED), "we should ship friday").reason == "proactive"


def test_app_initiated_speech_allowed_but_still_bound_by_silence_and_takeover():
    c = cfg()
    assert ev(c, "", origin="app").verdict is Verdict.ALLOW
    assert ev(c, "", origin="app", takeover_active=True).verdict is Verdict.DENY
    assert ev(cfg(mode=Mode.OBSERVE, policy=SpeakingPolicy.SILENT), "", origin="app").verdict is Verdict.DENY


def test_takeover_needs_trusted_role_and_reliable_identity_and_mute_always_works():
    c = group_cfg()
    assert ev(c, "Thanks Tara, I'll take it from here", R).verdict is Verdict.TAKEOVER
    assert ev(c, "I'll take it from here", Attribution("a", 0.99)).reason == "takeover_not_authorized"   # a candidate
    assert ev(c, "I'll take it from here", Attribution(None, 0.0)).verdict is Verdict.DENY
    assert ev(c, "I'll take it from here", Attribution("r", 0.4)).verdict is Verdict.DENY
    assert ev(cfg(takeover_phrases_enabled=False), "I'll take it from here", R).verdict is Verdict.DENY
    assert ev(c, "Tara, please be quiet", A).reason == "command_denied:listen_only:identity_low"   # interviews: only a trusted role may silence Tara
    quiet = ev(c, "Tara, please be quiet", R)
    assert quiet.verdict is Verdict.COMMAND and quiet.command.kind.value == "listen_only"
    assert ev(c, "please be quiet", A, tara_speaking=True).verdict is Verdict.MUTE      # said while Tara is talking: for her
    assert ev(c, "Alex, please be quiet for a second", A).verdict is Verdict.DENY      # said to someone else: no mute
    assert ev(c, "please be quiet", A).reason == "not_addressed"


def test_interview_requires_reliable_identity_to_answer():
    c = group_cfg()
    assert ev(c, "Tara, is my answer okay?", Attribution("a", 0.97)).verdict is Verdict.ALLOW
    assert ev(c, "Tara, is my answer okay?", Attribution("a", 0.4)).reason == "speaker_identity_low"
    assert ev(c, "Tara, is my answer okay?", A).reason == "speaker_identity_low"
    assert ev(c, "Tara, is my answer okay?", Attribution("a", 0.99, overlap=True)).reason == "speaker_identity_low"
    assert ev(group_cfg(), "we should ship friday", Attribution("a", 0.99)).reason == "not_addressed"


# ---- memory, registry, tool declarations -----------------------------------------------------------------------------
def test_working_memory_is_bounded_and_renders_newest():
    w = WorkingMemory(max_turns=3, summary="we discussed X")
    for i in range(10):
        w.add_turn("A", f"turn {i}")
    recent = w.render().split("Recent conversation:\n", 1)[1]
    assert len(w.turns) == 3 and "turn 9" in recent and "turn 0" not in recent and "discussed X" in w.render()
    assert "turn 0" in w.render()                                              # older turns survive as a short digest
    assert WorkingMemory().render() == ""


def test_meeting_memory_needs_provenance_and_is_scope_isolated():
    m = InMemoryMeetingMemory()
    a, b = Scope("t1", "m1"), Scope("t2", "m1")
    m.add(a, MemoryRecord("decision", "ship friday", ("e1",)))
    m.add(a, MemoryRecord("action_item", "write docs", ("e2",)))
    assert [r.body for r in m.list(a)] == ["ship friday", "write docs"] and m.list(a, "decision")[0].status == "candidate"
    assert m.list(b) == [] and m.list(Scope("t1", "m2")) == []            # same meeting id, other tenant; same tenant, other meeting
    with pytest.raises(ValueError):
        m.add(a, MemoryRecord("decision", "no source", ()))
    with pytest.raises(ValueError):
        m.add(a, MemoryRecord("decision", "x", ("e",), status="confirmed"))


def test_session_registry_limits_duplicates_and_release():
    r = SessionRegistry(2)
    r.acquire("a"); r.acquire("b")
    with pytest.raises(SessionLimitExceeded):
        r.acquire("c")
    with pytest.raises(DuplicateSession):
        r.acquire("a")
    r.release("a"); r.release("zz")
    r.acquire("c")
    assert r.active == 2
    with pytest.raises(ValueError):
        SessionRegistry(0)


def test_tool_declarations_show_only_granted_registered_tools_and_scope_is_not_model_supplied():
    async def go():
        kb = InMemoryKnowledge([Passage("p1", "refunds are processed in five days")])
        scope = Scope("t1", "m1")
        reg = {"search_knowledge": knowledge_tool(kb, scope), "crm_note": ToolDef("crm_note", {"text": str}, lambda text: 1)}
        ex = ToolExecutor(reg, (ToolSpec("search_knowledge", "r", False),))
        decls = tool_declarations(ex)
        assert [d.name for d in decls] == ["search_knowledge"]               # crm_note is registered but not granted
        assert decls[0].parameters == {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}
        res = await ex.execute_read_only(__import__("tara.contracts", fromlist=["ToolCall"]).ToolCall(
            "search_knowledge", {"query": "refunds", "tenant_id": "other"}), "c")
        assert not res.ok and res.error == "bad_args:unknown:tenant_id"        # the model cannot pass a scope
        ok = await ex.execute_read_only(__import__("tara.contracts", fromlist=["ToolCall"]).ToolCall(
            "search_knowledge", {"query": "refunds"}), "c2")
        assert ok.ok and ok.output[0]["id"] == "p1"
        assert tool_declarations(None) == ()
        assert await scoped_search(kb, scope)("refunds") != []
    run(go())
