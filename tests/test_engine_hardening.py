"""Failure-injection tests for MeetingEngine (fakes only)."""
import asyncio
from test_engine import U, build, run, settle
from helpers import cfg, group_cfg
from tara import events as E
from tara.adapters.local import LocalMeetingAdapter
from tara.audio import silence, tone
from tara.config import ToolSpec
from tara.contracts import MeetingEvent, MeetingEventKind as MK, PCMChunk, Transcript
from tara.engine import EngineParams
from tara.fakes import FakeLLMProvider, FakeStt
from tara.runtime import State
from tara.tools import ToolDef, ToolExecutor


def audio(eng_or_none, pcm, stream="mix", t0=0):
    return [PCMChunk(pcm[i:i + 640], 16000, t0 + i // 32, stream) for i in range(0, len(pcm), 640)]


class SlowStt:
    def __init__(self, delay, text="hello there"):
        self.delay, self.text, self.started = delay, text, 0

    async def transcribe(self, seg):
        self.started += 1
        await asyncio.sleep(self.delay)
        return Transcript(self.text, seg.start_ms, seg.end_ms)


def test_slow_stt_does_not_delay_barge_in_detection():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), stt=SlowStt(0.4, "ok sure"), slow=True)
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what is the plan for release?"))
        await asyncio.sleep(0.3)
        assert eng.rt.state is State.SPEAKING
        for ch in audio(eng, tone(300) + silence(400)):
            await eng.ingest_audio(ch)
        # STT is still sleeping, yet playback must already be cancelled
        assert eng.rt.state in (State.INTERRUPTED, State.EVALUATING_TURN, State.LISTENING)
        assert eng.spoken[0]["outcome"] == "interrupted"
        await eng.drain()
        assert eng.rt.state is State.LISTENING
    run(go())


def test_segment_queue_is_bounded_and_drops_oldest():
    async def go():
        gate = asyncio.Event()

        class Blocked:
            async def transcribe(self, seg):
                await gate.wait()
                return Transcript("", seg.start_ms, seg.end_ms)

        eng, ad, _ = build(cfg_=cfg(), stt=Blocked(), params=EngineParams(segment_queue=2))
        await eng.start()
        for n in range(6):
            for ch in audio(eng, tone(200) + silence(400), t0=n * 1000):
                await eng.ingest_audio(ch)
        assert eng._segq.qsize() <= 2 and eng.dropped_segments >= 2
        gate.set()
        await eng.drain()
        assert E_names(eng).count("segment.dropped") == eng.dropped_segments
    run(go())


def E_names(eng):
    return eng.bus.names()


def test_unexpected_exception_in_generation_ends_in_listening_not_wedged():
    async def go():
        def boom(r, a):
            raise RuntimeError("bug")
        eng, ad, llm = build(cfg_=cfg(), llm=FakeLLMProvider(boom))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what is the plan for release?"))
        await settle(eng)
        assert eng.rt.state is State.LISTENING and eng.spoken == []
        assert any(e.name == E.PROPOSAL_REJECTED and str(e.data.get("reason", "")).startswith("internal") for e in eng.bus.history)
    run(go())


def test_tool_is_not_executed_when_proposal_goes_stale():
    async def go():
        ran = []
        reg = {"crm_note": ToolDef("crm_note", {"text": str}, lambda text: ran.append(text))}
        tools = ToolExecutor(reg, (ToolSpec("crm_note", "append", False),))
        llm = FakeLLMProvider(lambda r, a: {"say": "Noted.", "needs_tool": {"name": "crm_note", "args": {"text": "x"}}}, latency_s=0.3)
        eng, ad, _ = build(cfg_=cfg(), llm=llm)
        eng.tools = tools
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, note that for the record?"))
        await eng.handle_utterance(U("2", "a", "unrelated chat about lunch"))      # stale
        await asyncio.sleep(0.5)
        assert ran == [] and eng.spoken == []
    run(go())


def test_tool_runs_once_after_approval_and_blocked_by_takeover():
    async def go():
        ran = []
        reg = {"crm_note": ToolDef("crm_note", {"text": str}, lambda text: ran.append(text))}
        eng, ad, _ = build(cfg_=cfg(), llm=FakeLLMProvider(
            lambda r, a: {"say": "Noted.", "needs_tool": {"name": "crm_note", "args": {"text": "x"}}}))
        eng.tools = ToolExecutor(reg, (ToolSpec("crm_note", "append", False),))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, note that for the record?"))
        await settle(eng)
        assert ran == ["x"] and eng.spoken[0]["outcome"] == "completed"
        await eng.handle_utterance(U("2", "r", "Thanks Tara, I'll take it from here."))
        await eng.handle_utterance(U("3", "r", "Tara, note that for the record too?"))
        assert ran == ["x"]
    run(go())


def test_waits_for_floor_then_speaks():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), llm=FakeLLMProvider(lambda r, a: {"say": "Ok."}))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what is the plan?"))
        eng.rt.others_speaking.add("a")           # someone starts talking while Tara was generating
        await asyncio.sleep(0.15)
        assert eng.rt.state is State.WAITING_TO_SPEAK and eng.spoken == []
        eng.rt.others_speaking.clear()
        await settle(eng)
        assert eng.spoken and eng.spoken[0]["outcome"] == "completed"
    run(go())


def test_floor_wait_times_out_and_drops_answer():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), llm=FakeLLMProvider(lambda r, a: {"say": "Ok."}),
                             params=EngineParams(floor_wait_ms=100))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what is the plan?"))
        eng.rt.others_speaking.add("a")
        await settle(eng)
        assert eng.spoken == [] and eng.rt.state is State.LISTENING
        assert any(e.data.get("reason") == "floor_timeout" for e in eng.bus.history)
    run(go())


def test_deferred_trigger_answered_when_floor_frees_and_expires_otherwise():
    async def go():
        eng, ad, llm = build(cfg_=cfg(), llm=FakeLLMProvider(lambda r, a: {"say": "Welcome."}),
                             params=EngineParams(defer_max_age_ms=300))
        await eng.start()
        eng.rt.others_speaking.add("a")
        await eng.handle_utterance(U("1", "r", "Tara, thanks for joining us"))   # addressed, not a question
        assert eng._deferred is not None and eng.spoken == []
        eng.rt.others_speaking.clear()
        await eng.tick()
        await settle(eng)
        assert eng.spoken and eng._deferred is None
        # expiry path
        eng.rt.others_speaking.add("a")
        await eng.handle_utterance(U("2", "r", "Tara, thanks again for the notes"))
        await asyncio.sleep(0.4)
        eng.rt.others_speaking.clear()
        await eng.tick()
        assert eng._deferred is None and len(eng.spoken) == 1
    run(go())


def test_malformed_adapter_payloads_do_not_crash():
    async def go():
        eng, ad, _ = build(cfg_=cfg())
        await eng.start()
        await eng.handle_event(MeetingEvent(MK.PARTICIPANT_JOINED, 0, "m1", {"id": "z"}))          # missing name
        await eng.handle_event(MeetingEvent(MK.PARTICIPANT_RENAMED, 0, "m2", {"id": "nobody", "name": "x"}))
        await eng.handle_event(MeetingEvent(MK.AUDIO, 0, "m3", {}))
        await eng.ingest_audio(PCMChunk(b"\x00\x00\x00", 16000, 0, "mix"))                          # odd length
        await eng.ingest_audio(PCMChunk(b"\x00\x00", 0, 0, "mix"))                                    # bad rate
        names = eng.bus.names()
        assert names.count("event.invalid") == 3 and names.count("audio.invalid") == 2
        assert eng.rt.state is State.LISTENING
    run(go())


def test_stt_timeout_is_reported_and_recovers():
    async def go():
        eng, ad, _ = build(cfg_=cfg(), stt=SlowStt(1.0), params=EngineParams(stt_timeout_s=0.05))
        await eng.start()
        for ch in audio(eng, tone(300) + silence(400)):
            await eng.ingest_audio(ch)
        await eng.drain()
        assert "transcript.failed" in eng.bus.names() and eng.rt.state is State.LISTENING
    run(go())


def test_hung_playback_is_cut_by_watchdog_and_not_assumed_delivered():
    async def go():
        class Hung(LocalMeetingAdapter):
            async def send_audio(self, uid, chunks):
                self.sent[uid] = 0
                await asyncio.sleep(10)
        eng, ad, _ = build(cfg_=cfg(), adapter=Hung(), params=EngineParams(max_speech_s=0.1))
        await eng.start()
        await eng.handle_utterance(U("1", "r", "Tara, what is the plan?"))
        await settle(eng)
        assert eng.spoken[0]["outcome"] == "failed" and eng.rt.state is State.LISTENING
        assert E.SPEECH_DELIVERED not in eng.bus.names()
    run(go())


def test_events_after_shutdown_are_ignored_and_shutdown_is_idempotent():
    async def go():
        eng, ad, _ = build(cfg_=cfg())
        await eng.start()
        await eng.shutdown(); await eng.shutdown()
        await eng.handle_event(MeetingEvent(MK.PARTICIPANT_JOINED, 0, "m1", {"id": "q", "name": "Q"}))
        await eng.ingest_audio(PCMChunk(tone(100), 16000, 0, "mix"))
        assert eng.registry.get("q") is None and eng.rt.state is State.STOPPED
    run(go())


def test_runtime_answered_and_seen_sets_stay_bounded():
    from tara.runtime import MeetingRuntime
    rt = MeetingRuntime(1000, dedupe_window=50)
    for i in range(1000):
        rt.accept_event(f"e{i}"); rt.mark_answered(f"t{i}")
    assert len(rt._seen) == 50 and len(rt._answered) == 50


def test_group_interview_end_to_end_keeps_candidates_separate_through_audio_path():
    """Per-participant streams: A and B speak on their own streams; B interrupts A."""
    async def go():
        eng, ad, _ = build(cfg_=group_cfg(), stt=FakeStt(queue=["use an idempotency key", "no, a queue is better"]))
        await eng.start()
        for ch in audio(eng, silence(200) + tone(500) + silence(500), "a"):
            await eng.ingest_audio(ch)
        for ch in audio(eng, silence(200) + tone(500) + silence(500), "b", t0=2000):
            await eng.ingest_audio(ch)
        await eng.drain()
        atts = [e.data for e in eng.bus.history if e.name == E.ATTRIBUTION]
        assert [x["participant"] for x in atts] == ["a", "b"]
        assert all(x["confidence"] >= 0.9 for x in atts)
    run(go())
