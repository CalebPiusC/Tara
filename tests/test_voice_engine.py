"""LiveVoiceEngine end to end with a scripted provider and a local meeting adapter.
Proves orchestration/policy only: nothing here talks to Google or a real meeting."""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from helpers import ROLES, TH, cfg, group_cfg

from tara.adapters.local import LocalMeetingAdapter
from tara.audio import silence, tone
from tara.classify import RuleIntentClassifier
from tara.config import ToolSpec
from tara.contracts import MeetingEvent, PCMChunk
from tara.contracts import MeetingEventKind as MK
from tara.errors import DuplicateSession, FatalProviderError, ProviderError, SessionLimitExceeded
from tara.metrics import Metrics
from tara.tools import ToolDef, ToolExecutor
from tara.voice.engine import LiveParams, LiveVoiceEngine
from tara.voice.events import (
    Disconnected,
    GenerationComplete,
    GoAway,
    InputTranscript,
    Interrupted,
    OutputTranscript,
    ResponseAudio,
    ResumptionHandle,
    ToolCallRequested,
    ToolCallsCancelled,
    ToolRequest,
    TurnComplete,
    VoiceSessionConfig,
)
from tara.voice.fakes import FakeVoiceProvider, FakeVoiceSession
from tara.voice.memory import Scope
from tara.voice.participation import LiveParticipation
from tara.voice.registry import SessionRegistry

run = asyncio.run
NAMES = {"a": "Candidate A", "b": "Candidate B", "r": "Recruiter"}
CH = b"\x00\x00" * 2400          # 100 ms of 24 kHz PCM16


def audio(rid, n=1):
    return [ResponseAudio(rid, CH) for _ in range(n)]


def mic(ms=400, stream="mix", t0=0):
    pcm = tone(ms)
    return [PCMChunk(pcm[i:i + 640], 16000, t0 + i // 32, stream) for i in range(0, len(pcm), 640)]


def hush(stream, t0=2000):
    """500 ms of silence so the local VAD closes the participant's speech segment."""
    return [PCMChunk(silence(500), 16000, t0, stream)]


async def until(cond, timeout=10.0):
    end = asyncio.get_event_loop().time() + timeout
    while not cond():
        if asyncio.get_event_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


def mk(c=None, sessions=None, failures=None, params=None, tools=None, adapter=None, registry=None,
       session_id="s1", scope=None, factor=1.0, handle_cfg=None):
    c = c or cfg()
    adapter = adapter or LocalMeetingAdapter(realtime_factor=factor)
    sessions = sessions if sessions is not None else [FakeVoiceSession()]
    prov = FakeVoiceProvider(list(sessions), failures)
    m = Metrics()
    eng = LiveVoiceEngine(
        session_id=session_id, scope=scope or Scope("t1", "m1"), cfg=c, roles=ROLES, names=NAMES, provider=prov,
        session_config=handle_cfg or VoiceSessionConfig("sys"), adapter=adapter,
        participation=LiveParticipation(c, ROLES, RuleIntentClassifier(), TH), registry=registry or SessionRegistry(4),
        tools=tools, params=params or LiveParams(hold_ms=300, reconnect_backoff_s=0.01), metrics=m)
    return eng, sessions, prov, adapter


@asynccontextmanager
async def running(eng):
    await eng.start()
    try:
        yield eng
    finally:
        await eng.close()


def test_start_close_lifecycle_releases_everything_and_is_idempotent():
    async def go():
        reg = SessionRegistry(1)
        eng, (s,), prov, ad = mk(registry=reg)
        await eng.start()
        assert eng.status == "active" and ad.joined and reg.active == 1 and prov.opened[0][1] is None
        assert eng.metrics.summary("live_connect_ms")["count"] == 1
        with pytest.raises(SessionLimitExceeded):                      # second concurrent session refused
            e2, _, _, _ = mk(registry=reg, session_id="s2")
            await e2.start()
        await eng.close(); await eng.close()
        assert eng.status == "closed" and s.closed and not ad.joined and reg.active == 0
        assert not eng._tasks or all(t.done() for t in eng._tasks)
        assert eng.metrics.counters["state_invalid"] == 0
    run(go())


def test_failed_start_cleans_up_and_frees_the_slot():
    async def go():
        reg = SessionRegistry(1)
        eng, _, _, ad = mk(registry=reg, sessions=[], failures=[ProviderError("no")])
        with pytest.raises(ProviderError):
            await eng.start()
        assert reg.active == 0 and eng.status == "closed" and not ad.joined
        eng2, _, _, _ = mk(registry=reg, session_id="s9")
        async with running(eng2):
            assert reg.active == 1
        with pytest.raises(DuplicateSession):
            r2 = SessionRegistry(3); r2.acquire("same")
            await mk(registry=r2, session_id="same")[0].start()
    run(go())


def test_uplink_resamples_in_order_ignores_own_track_and_invalid_audio():
    async def go():
        eng, (s,), _, _ = mk()
        async with running(eng):
            await eng.on_meeting_audio(PCMChunk(b"\x01\x00" * 480, 48000, 0, "a"))      # 10 ms at 48 kHz -> 160 samples
            await eng.on_meeting_audio(PCMChunk(b"\x02\x00" * 160, 16000, 10, "a"))
            await eng.on_meeting_audio(PCMChunk(tone(100), 16000, 20, "tara"))          # Tara's own track
            await eng.on_meeting_audio(PCMChunk(b"\x00\x00\x00", 16000, 30, "a"))       # odd length
            await until(lambda: len(s.audio) == 2)
            assert len(s.audio[0]) == 320 and s.audio[1][:2] == b"\x02\x00"
            assert eng.metrics.counters["audio_invalid"] == 1
    run(go())


def test_uplink_backpressure_drops_oldest_and_never_blocks_the_caller():
    async def go():
        s = FakeVoiceSession()
        s.send_gate = asyncio.Event()                                  # the network is stuck
        eng, _, _, _ = mk(sessions=[s], params=LiveParams(uplink_queue=5, hold_ms=300))
        async with running(eng):
            for i in range(40):
                await eng.on_meeting_audio(PCMChunk(b"\x01\x00" * 160, 16000, i, "mix"))
            assert eng._uplink.qsize() <= 5 and eng.metrics.counters["live_uplink_dropped"] >= 30
            s.send_gate.set()
            await until(lambda: len(s.audio) >= 1)
    run(go())


def test_addressed_turn_is_played_to_completion_and_remembered():
    async def go():
        eng, (s,), _, ad = mk(factor=0.1)
        async with running(eng):
            s.push(InputTranscript("Tara, what is the plan for release?", True), *audio(1, 3), GenerationComplete(1), TurnComplete(1))
            await until(lambda: eng.playback.results)
            r = eng.playback.results[0]
            assert r.completed and r.delivered_ms == 300 and ad.completed["live_1"]
            assert eng.metrics.counters["gate_allowed"] == 1
            assert eng.metrics.counters["live_input_transcripts"] == 1
            assert eng.metrics.counters["live_response_audio_chunks"] == 3
            assert eng.metrics.counters["live_turn_complete"] == 1
            assert any(t[0] == "participant" for t in eng.working.turns)
            assert eng.metrics.summary("live_response_ms")["count"] == 1
            assert eng.state.response_task is None and not eng.state.tara_speaking
            assert eng.metrics.counters["state_invalid"] == 0
    run(go())


def test_turn_not_addressed_to_tara_is_never_played():
    async def go():
        eng, (s,), _, ad = mk(factor=0.1)
        async with running(eng):
            s.push(InputTranscript("we should ship on friday", True), *audio(1, 3), TurnComplete(1))
            await until(lambda: eng.metrics.counters["gate_denied"] == 1)
            await asyncio.sleep(0.05)
            assert ad.sent == {} and eng.playback.results == []
            assert eng.working.turns[-1] == ("participant", "we should ship on friday")
    run(go())


def test_audio_before_transcript_is_held_then_released_in_order():
    async def go():
        eng, (s,), _, ad = mk(factor=0.1, params=LiveParams(hold_ms=1000))
        async with running(eng):
            s.push(*audio(1, 2))
            await until(lambda: eng._gates and len(eng._gates[1].held) == 2)
            assert ad.sent == {}                                            # nothing audible while undecided
            s.push(InputTranscript("Tara, thoughts?", True), *audio(1, 1), TurnComplete(1))
            await until(lambda: eng.playback.results)
            assert eng.playback.results[0].delivered_ms == 300
    run(go())


def test_soft_denial_flips_when_the_name_arrives_late_and_hold_timeout_fails_silent():
    async def go():
        eng, (s,), _, ad = mk(factor=0.1, params=LiveParams(hold_ms=1000))
        async with running(eng):
            s.push(InputTranscript("what do you think about", False), *audio(1, 2))
            await until(lambda: eng._gates and eng._gates[1].state == "pending")
            s.push(InputTranscript("it Tara?", True))                     # addressee only revealed later
            await until(lambda: eng._gates[1].state == "allowed")
            s.push(TurnComplete(1))
            await until(lambda: eng.playback.results)
        eng2, (s2,), _, ad2 = mk(factor=0.1, params=LiveParams(hold_ms=80, transcript_grace_s=0.0))
        async with running(eng2):
            s2.push(*audio(1, 3))                                          # no transcript ever arrives
            await until(lambda: eng2.metrics.counters["gate_denied"] == 1)
            assert ad2.sent == {} and eng2.metrics.counters["gate_allowed"] == 0
    run(go())


def test_provider_interruption_stops_playback_drops_stale_audio_keeps_session_and_next_reply_plays():
    async def go():
        eng, (s,), _, ad = mk(factor=1.0)
        async with running(eng):
            s.push(InputTranscript("Tara, give me the full plan", True), *audio(1, 10),
                   OutputTranscript("Here is the full plan for the release team", 1))
            await until(lambda: eng.playback.is_playing)
            await asyncio.sleep(0.25)
            await eng.on_meeting_audio(PCMChunk(b"\x01\x00" * 160, 16000, 0, "a"))       # participant audio keeps flowing
            s.push(Interrupted(1))
            await until(lambda: eng.metrics.counters["live_interrupts.provider"] == 1)
            assert not eng.playback.is_playing and ad.completed["live_1"] is False
            assert 0 < ad.sent["live_1"] < 1000                                        # partial delivery recorded
            s.push(*audio(1, 5))                                                        # late chunks of the dead response
            await until(lambda: eng.metrics.counters["stale_audio_dropped"] == 5)
            assert eng.status == "active" and not s.closed                              # the meeting session survives
            assert any(t[1].endswith("[interrupted]") for t in eng.working.turns)       # only the heard part is remembered
            s.push(InputTranscript("Tara, the plan we already tried", True), *audio(2, 2), TurnComplete(2))
            await until(lambda: len(eng.playback.results) == 2)
            assert eng.playback.results[1].response_id == 2 and eng.playback.results[1].completed
            await until(lambda: len(s.audio) >= 1)                                      # the interrupter's speech reached the model
            assert eng.metrics.summary("live_interrupt_stop_ms")["count"] >= 1
            assert eng.metrics.counters["state_invalid"] == 0 and eng.epoch >= 1
    run(go())


def test_local_barge_in_stops_playback_before_the_provider_reports_it():
    async def go():
        eng, (s,), _, ad = mk(factor=1.0)
        async with running(eng):
            s.push(InputTranscript("Tara, explain the architecture", True), *audio(1, 10))
            await until(lambda: eng.playback.is_playing)
            await asyncio.sleep(0.2)
            for ch in mic(400, "a"):                                      # a participant starts talking
                await eng.on_meeting_audio(ch)
            await until(lambda: not eng.playback.is_playing)
            for ch in hush("a"):                                           # ...and finishes
                await eng.on_meeting_audio(ch)
            assert eng.metrics.counters["playback_cancelled.local_interrupt"] == 1
            s.push(*audio(1, 4))                                           # server has not interrupted yet: same response keeps coming
            await until(lambda: eng.metrics.counters["stale_audio_dropped"] == 4)
            s.push(Interrupted(1))
            await until(lambda: eng.metrics.counters["live_interrupts.provider"] == 1)
            s.push(InputTranscript("Tara, answer this too", True), *audio(2, 1), TurnComplete(2))
            await until(lambda: any(r.response_id == 2 for r in eng.playback.results))
    run(go())


def test_local_barge_in_keeps_the_interrupted_turn_and_starts_a_new_turn():
    async def go():
        eng, (s,), _, _ = mk(factor=1.0)
        async with running(eng):
            for ch in mic(300, "a") + hush("a"):
                await eng.on_meeting_audio(ch)
            s.push(InputTranscript("Tara, explain the architecture", True), *audio(1, 10))
            await until(lambda: eng.playback.is_playing)
            await asyncio.sleep(0.2)
            for ch in mic(400, "b"):                                  # a different participant barges in
                await eng.on_meeting_audio(ch)
            await until(lambda: not eng.playback.is_playing)
            for ch in hush("b"):
                await eng.on_meeting_audio(ch)
            s.push(InputTranscript("Tara, what about caching?", True), *audio(2, 2), TurnComplete(2))
            await until(lambda: any("what about caching" in t[1] for t in eng.working.turns))
            turns = list(eng.working.turns)
            assert ("Candidate A", "Tara, explain the architecture") in turns
            assert ("Candidate B", "Tara, what about caching?") in turns
            assert not any("architecture Tara" in t[1] for t in turns)
    run(go())


def test_short_noise_never_cancels_playback_and_local_barge_in_can_be_disabled():
    async def go():
        eng, (s,), _, ad = mk(factor=1.0)
        async with running(eng):
            s.push(InputTranscript("Tara, explain", True), *audio(1, 6))
            await until(lambda: eng.playback.is_playing)
            for ch in mic(60, "a"):                                         # a click, shorter than min_local_speech_ms
                await eng.on_meeting_audio(ch)
            for ch in [PCMChunk(silence(100), 16000, 500, "a")]:
                await eng.on_meeting_audio(ch)
            await asyncio.sleep(0.1)
            assert eng.playback.is_playing and eng.metrics.counters["live_interrupts.local"] == 0
        eng2, (s2,), _, _ = mk(factor=1.0, params=LiveParams(hold_ms=300, local_barge_in=False))
        async with running(eng2):
            s2.push(InputTranscript("Tara, explain", True), *audio(1, 6))
            await until(lambda: eng2.playback.is_playing)
            for ch in mic(400, "a"):
                await eng2.on_meeting_audio(ch)
            assert eng2.playback.is_playing                                  # only the provider may interrupt now
    run(go())


def test_takeover_by_recruiter_stops_speech_and_blocks_until_released():
    async def go():
        eng, (s,), _, ad = mk(c=group_cfg(), factor=1.0)
        async with running(eng):
            s.push(InputTranscript("Tara, explain the interview format.", True), *audio(1, 10))
            # identify the speaker as the recruiter through a per-participant stream
            for ch in mic(300, "r") + hush("r", 400):
                await eng.on_meeting_audio(ch)
            await until(lambda: eng.playback.is_playing or eng.metrics.counters["live_interrupts.local"] == 1)
            s.push(Interrupted(1), InputTranscript("Thanks Tara, I'll take it from here", True), *audio(2, 3))
            await until(lambda: eng.takeover_active)
            assert not eng.playback.is_playing and eng.state.user_has_control and eng.metrics.counters["live_takeovers"] == 1
            s.push(TurnComplete(2), InputTranscript("Tara, one more thing please", True), *audio(3, 2), TurnComplete(3))
            await until(lambda: eng.metrics.counters["gate_denied"] >= 2)
            assert not any(r.response_id == 3 for r in eng.playback.results)
            eng.release_takeover()
            assert not eng.takeover_active and not eng.state.user_has_control
    run(go())


def test_candidate_cannot_take_over_and_mute_request_silences_tara():
    async def go():
        eng, (s,), _, ad = mk(c=group_cfg(), factor=0.1)
        async with running(eng):
            for ch in mic(300, "a"):
                await eng.on_meeting_audio(ch)
            s.push(InputTranscript("I'll take it from here", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: eng.metrics.counters["gate_denied"] == 1)
            assert not eng.takeover_active
        eng2, (s2,), _, _ = mk(factor=0.1)
        async with running(eng2):
            s2.push(InputTranscript("Tara, please be quiet for a bit", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: eng2.muted)
            s2.push(InputTranscript("Tara, what do you think?", True), *audio(2, 2), TurnComplete(2))
            await until(lambda: eng2.metrics.counters["gate_denied"] >= 2)
            assert eng2.playback.results == []
            eng2.unmute()
            s2.push(InputTranscript("Tara, what do you think now?", True), *audio(3, 1), TurnComplete(3))
            await until(lambda: eng2.playback.results)
    run(go())


def test_app_initiated_prompt_plays_without_transcript_but_not_when_silent():
    async def go():
        eng, (s,), _, ad = mk(c=group_cfg(), factor=0.1)
        async with running(eng):
            await eng.prompt("Ask candidate A the first question.")
            assert s.texts == ["Ask candidate A the first question."]
            s.push(*audio(1, 2), TurnComplete(1))
            await until(lambda: eng.playback.results and eng.playback.results[0].completed)
        from tara.config import Mode
        quiet = cfg(mode=Mode.OBSERVE, policy=__import__("tara.config", fromlist=["SpeakingPolicy"]).SpeakingPolicy.SILENT)
        eng2, (s2,), _, _ = mk(c=quiet, factor=0.1)
        async with running(eng2):
            await eng2.prompt("say something")
            s2.push(*audio(1, 2), TurnComplete(1))
            await until(lambda: eng2.metrics.counters["gate_denied"] == 1)
            assert eng2.playback.results == []
    run(go())


# ---- tools -------------------------------------------------------------------------------------------------------
def tool_exec(slow=0.0):
    calls = []

    async def lookup(query):
        await asyncio.sleep(slow)
        calls.append(query)
        return [{"id": "p1", "text": "refunds take five days"}]

    reg = {"lookup": ToolDef("lookup", {"query": str}, lookup, description="d", side_effect=False, timeout_s=2.0),
           "crm_note": ToolDef("crm_note", {"text": str}, lambda text: calls.append("WROTE"), side_effect=True),
           "secret": ToolDef("secret", {}, lambda: calls.append("SECRET"), side_effect=False)}
    ex = ToolExecutor(reg, (ToolSpec("lookup", "r", False), ToolSpec("crm_note", "w", False)))   # "secret" is NOT granted
    return ex, calls


def test_tool_calls_are_validated_gated_and_results_returned_to_the_model():
    async def go():
        ex, calls = tool_exec()
        eng, (s,), _, _ = mk(tools=ex)
        async with running(eng):
            s.push(ToolCallRequested((ToolRequest("1", "lookup", {"query": "refund window"}),
                                      ToolRequest("2", "crm_note", {"text": "x"}),
                                      ToolRequest("3", "secret", {}),
                                      ToolRequest("4", "lookup", {"query": 5}),
                                      ToolRequest("5", "lookup", {"query": "a", "tenant_id": "other"}))))
            await until(lambda: len(s.tool_results) == 5)
            by = {cid: res for cid, _, res in s.tool_results}
            assert by["1"] == {"result": [{"id": "p1", "text": "refunds take five days"}]}
            assert by["2"]["error"] == "side_effect_requires_approval" and eng.approvals_needed == ["crm_note"]
            assert by["3"]["error"] == "not_granted" and by["4"]["error"].startswith("bad_args") and by["5"]["error"].startswith("bad_args")
            assert all("never stay silent" in by[k]["say"].lower() for k in ("2", "3", "4", "5"))     # failures always carry the speak hint
            assert calls == ["refund window"]                                # the side-effect and ungranted tools NEVER ran
    run(go())


def test_slow_tool_does_not_block_audio_and_cancellation_suppresses_its_result():
    async def go():
        ex, calls = tool_exec(slow=0.5)
        eng, (s,), _, _ = mk(tools=ex)
        async with running(eng):
            s.push(ToolCallRequested((ToolRequest("9", "lookup", {"query": "slow"}),)))
            await until(lambda: "9" in eng._tool_tasks)
            t0 = asyncio.get_event_loop().time()
            for i in range(5):
                await eng.on_meeting_audio(PCMChunk(b"\x01\x00" * 160, 16000, i, "mix"))
            await until(lambda: len(s.audio) == 5)
            assert asyncio.get_event_loop().time() - t0 < 0.4                 # audio flowed while the tool was pending
            s.push(ToolCallsCancelled(("9",)))
            await asyncio.sleep(0.7)
            assert s.tool_results == [] and calls == [] and eng.metrics.counters["live_tool_cancelled"] == 1
    run(go())


def test_no_tools_configured_returns_an_error_not_a_hang():
    async def go():
        eng, (s,), _, _ = mk(tools=None)
        async with running(eng):
            s.push(ToolCallRequested((ToolRequest("1", "anything", {}),)))
            await until(lambda: s.tool_results)
            assert s.tool_results[0][2] == {"error": "tools_unavailable"}
    run(go())


# ---- reconnect and failure ----------------------------------------------------------------------------------------------
def test_recoverable_disconnect_cancels_speech_reconnects_with_resume_handle_and_keeps_streaming():
    async def go():
        s1, s2 = FakeVoiceSession(), FakeVoiceSession()
        eng, _, prov, ad = mk(sessions=[s1, s2], factor=1.0)
        async with running(eng):
            s1.push(ResumptionHandle("handle-7", True), InputTranscript("Tara, explain it", True), *audio(1, 10))
            await until(lambda: eng.playback.is_playing)
            s1.push(Disconnected("network", True))
            await until(lambda: eng.metrics.counters["live_reconnects"] == 1)
            assert not eng.playback.is_playing and ad.completed["live_1"] is False      # never assumes the old speech was delivered
            assert prov.opened[1][1] == "handle-7" and s1.closed and eng.status == "active"
            assert s2.contexts == []                                                      # resumed: no re-seeding needed
            await eng.on_meeting_audio(PCMChunk(b"\x01\x00" * 160, 16000, 0, "mix"))
            await until(lambda: len(s2.audio) == 1 and not s1.audio)
            s2.push(InputTranscript("Tara, continue", True), *audio(1, 1), TurnComplete(1))     # new session: provider ids restart at 1
            await until(lambda: any(r.completed and r.response_id > 1 for r in eng.playback.results))   # not mistaken for the dead response
    run(go())


def test_reconnect_without_handle_reseeds_the_new_session_from_working_memory():
    async def go():
        s1, s2 = FakeVoiceSession(), FakeVoiceSession()
        eng, _, prov, _ = mk(sessions=[s1, s2], factor=0.1)
        async with running(eng):
            eng.working.add_turn("Recruiter", "we agreed to ship on friday")
            s1.push(Disconnected("reset", True))
            await until(lambda: eng.metrics.counters["live_reconnects"] == 1)
            assert prov.opened[1][1] is None and "ship on friday" in s2.contexts[0]
    run(go())


def test_goaway_triggers_a_reconnect_and_attempts_are_bounded_then_session_fails_cleanly():
    async def go():
        s1, s2 = FakeVoiceSession(), FakeVoiceSession()
        eng, _, prov, _ = mk(sessions=[s1, s2])
        async with running(eng):
            s1.push(GoAway(5.0))
            await until(lambda: eng.metrics.counters["live_reconnects"] == 1)
        reg = SessionRegistry(1)
        s3 = FakeVoiceSession()
        eng2, _, prov2, _ = mk(sessions=[s3], failures=[], registry=reg, params=LiveParams(reconnect_attempts=2, reconnect_backoff_s=0.01, hold_ms=300))
        await eng2.start()
        prov2.failures = [ProviderError("down"), ProviderError("down")]
        s3.push(Disconnected("lost", True))
        await asyncio.wait_for(eng2.wait_closed(), 3)
        assert eng2.metrics.counters["live_reconnect_attempts"] == 2 and eng2.metrics.counters["live_session_failures"] == 1
        assert reg.active == 0 and eng2.status == "closed"
    run(go())


def test_fatal_errors_and_non_recoverable_disconnects_are_not_retried():
    async def go():
        for trigger in ("fatal_open", "not_recoverable"):
            s1 = FakeVoiceSession()
            eng, _, prov, _ = mk(sessions=[s1])
            await eng.start()
            if trigger == "fatal_open":
                prov.failures = [FatalProviderError("401")]
                s1.push(Disconnected("lost", True))
            else:
                s1.push(Disconnected("auth", False))
            await asyncio.wait_for(eng.wait_closed(), 3)
            assert eng.metrics.counters["live_reconnects"] == 0 and len(prov.opened) == (2 if trigger == "fatal_open" else 1)
    run(go())


def test_send_failure_is_treated_as_disconnect_and_recovers():
    async def go():
        s1, s2 = FakeVoiceSession(), FakeVoiceSession()
        s1.fail_send = True
        eng, _, _, _ = mk(sessions=[s1, s2])
        async with running(eng):
            await eng.on_meeting_audio(PCMChunk(b"\x01\x00" * 160, 16000, 0, "mix"))
            await until(lambda: eng.metrics.counters["live_reconnects"] == 1)
            assert eng.metrics.counters["live_send_errors"] >= 1
    run(go())


def test_tool_result_from_a_dead_session_is_dropped_after_reconnect():
    async def go():
        ex, calls = tool_exec(slow=0.3)
        s1, s2 = FakeVoiceSession(), FakeVoiceSession()
        eng, _, _, _ = mk(sessions=[s1, s2], tools=ex)
        async with running(eng):
            s1.push(ToolCallRequested((ToolRequest("1", "lookup", {"query": "x"}),)))
            await until(lambda: "1" in eng._tool_tasks)
            s1.push(Disconnected("x", True))
            await until(lambda: eng.metrics.counters["live_reconnects"] == 1)
            await asyncio.sleep(0.5)
            assert s1.tool_results == [] and s2.tool_results == []
    run(go())


def test_meeting_end_closes_and_meeting_reconnect_cancels_audible_speech():
    async def go():
        eng, (s,), _, ad = mk(factor=1.0)
        await eng.start()
        s.push(InputTranscript("Tara, explain", True), *audio(1, 10))
        await until(lambda: eng.playback.is_playing)
        ad.push(MeetingEvent(MK.RECONNECTED, 0, "r1"))
        await until(lambda: not eng.playback.is_playing)
        assert eng.status == "active" and ad.completed["live_1"] is False
        ad.push(MeetingEvent(MK.ENDED, 1, "end"))
        await asyncio.wait_for(eng.wait_closed(), 3)
        assert s.closed
    run(go())


# ---- isolation, secrets, instrumentation --------------------------------------------------------------------------
def test_two_sessions_do_not_share_state_memory_or_audio():
    async def go():
        reg = SessionRegistry(2)
        e1, (s1,), _, a1 = mk(registry=reg, session_id="A", scope=Scope("t1", "m1"), factor=0.1)
        e2, (s2,), _, a2 = mk(registry=reg, session_id="B", scope=Scope("t2", "m1"), factor=0.1)
        await e1.start(); await e2.start()
        try:
            s1.push(InputTranscript("Tara, secret plan alpha", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: e1.playback.results)
            await e2.on_meeting_audio(PCMChunk(b"\x01\x00" * 160, 16000, 0, "mix"))
            await until(lambda: len(s2.audio) == 1)
            assert s1.audio == [] and a2.sent == {} and e2.playback.results == []
            assert e2.working.turns == type(e2.working.turns)() and e1.working.turns
            assert "alpha" not in json.dumps([e.data for e in e2.bus.history], default=str)
            assert e1.state is not e2.state and e1.working is not e2.working and e1.scope != e2.scope
        finally:
            await e1.close(); await e2.close()
        assert reg.active == 0
    run(go())


def test_events_carry_ids_and_lengths_not_content_or_secrets():
    async def go():
        eng, (s,), _, _ = mk(factor=0.1)
        async with running(eng):
            s.push(InputTranscript("Tara, my password is hunter2", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: eng.playback.results)
            blob = json.dumps([(e.name, e.data) for e in eng.bus.history], default=str)
            assert "hunter2" not in blob and "session" in blob and "live.gate" in blob
            assert all(e.data.get("session") == "s1" for e in eng.bus.history)
    run(go())


def test_latency_metrics_are_recorded_only_when_measurable():
    async def go():
        eng, (s,), _, _ = mk(factor=0.1)
        async with running(eng):
            s.push(InputTranscript("Tara, hello", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: eng.playback.results)
            assert eng.metrics.summary("live_first_audio_ms") == {"count": 0}          # no local end-of-speech was observed
            assert eng.metrics.summary("live_first_playback_ms")["count"] == 1
            for ch in mic(300, "a") + [PCMChunk(silence(500), 16000, 400, "a")]:
                await eng.on_meeting_audio(ch)
            s.push(InputTranscript("Tara, again", True), *audio(2, 2), TurnComplete(2))
            await until(lambda: len(eng.playback.results) == 2)
            assert eng.metrics.summary("live_first_audio_ms")["count"] == 1           # measured from local end-of-speech
            assert eng.metrics.summary("live_connect_ms")["count"] == 1
    run(go())


def test_hybrid_end_of_speech_sends_audio_stream_end_only_when_enabled():
    async def go():
        eng, (s,), _, _ = mk(params=LiveParams(hold_ms=300, hybrid_end_of_speech=True))
        async with running(eng):
            for ch in mic(300, "a") + [PCMChunk(silence(500), 16000, 400, "a")]:
                await eng.on_meeting_audio(ch)
            assert s.stream_ends == 1
        eng2, (s2,), _, _ = mk()
        async with running(eng2):
            for ch in mic(300, "a") + [PCMChunk(silence(500), 16000, 400, "a")]:
                await eng2.on_meeting_audio(ch)
            assert s2.stream_ends == 0
    run(go())


def test_participant_events_and_malformed_meeting_events_do_not_break_the_session():
    async def go():
        eng, (s,), _, ad = mk()
        async with running(eng):
            ad.push(MeetingEvent(MK.PARTICIPANT_JOINED, 0, "e1", {"id": "z", "name": "Zed"}))
            ad.push(MeetingEvent(MK.PARTICIPANT_JOINED, 0, "e2", {"id": "q"}))             # malformed
            ad.push(MeetingEvent(MK.AUDIO, 0, "e3", {}))                                    # malformed
            ad.push(MeetingEvent(MK.PARTICIPANT_JOINED, 0, "e1", {"id": "dup", "name": "Dup"}))   # duplicate id
            await until(lambda: eng.metrics.counters["meeting_event_invalid"] == 2)
            assert eng.participants.get("z").display_name == "Zed" and eng.participants.get("dup") is None
            assert eng.status == "active"
    run(go())


def test_answer_after_a_tool_call_in_an_allowed_turn_is_not_dropped_for_missing_transcript():
    async def go():
        ex, _ = tool_exec()
        eng, (s,), _, _ = mk(tools=ex, factor=0.1, params=LiveParams(hold_ms=60, transcript_grace_s=0.0))
        async with running(eng):
            s.push(InputTranscript("Tara, what did we discuss last time?", True), *audio(1, 2))
            await until(lambda: eng._gates and eng._gates[1].state == "allowed")
            s.push(ToolCallRequested((ToolRequest("1", "lookup", {"query": "last time"}),)), TurnComplete(1))
            await until(lambda: len(s.tool_results) == 1)
            s.push(*audio(2, 3))                                 # the answer: no new input transcript exists
            await until(lambda: eng.metrics.counters["gate_allowed"] == 2)
            assert eng.metrics.counters["gate_denied"] == 0
    run(go())


def test_continuation_after_a_denied_turn_stays_denied():
    async def go():
        ex, _ = tool_exec()
        eng, (s,), _, _ = mk(tools=ex, factor=0.1, params=LiveParams(hold_ms=60, transcript_grace_s=0.0))
        async with running(eng):
            s.push(InputTranscript("so anyway the quarterly numbers look fine", True), *audio(1, 2))
            await until(lambda: eng._gates and eng._gates[1].state == "denied")
            s.push(ToolCallRequested((ToolRequest("1", "lookup", {"query": "x"}),)), TurnComplete(1))
            await until(lambda: len(s.tool_results) == 1)
            s.push(*audio(2, 3))
            await until(lambda: eng.metrics.counters["gate_denied"] == 2)
            assert eng.metrics.counters["gate_allowed"] == 0
    run(go())


def test_a_failed_tool_never_leaves_the_model_without_an_answer_and_silence_is_broken():
    async def go():
        ex, _ = tool_exec()
        eng, (s,), _, _ = mk(tools=ex, params=LiveParams(error_watch_s=0.1))
        async with running(eng):
            s.push(ToolCallRequested((ToolRequest("1", "secret", {}),)))                  # not granted: fails
            await until(lambda: len(s.tool_results) == 1)
            assert "never stay silent" in s.tool_results[0][2]["say"].lower()
            await until(lambda: eng.metrics.counters["live_error_prompts"] == 1)           # the model said nothing, so it is asked
            assert len(s.texts) == 1 and "failed" in s.texts[0] and "secret" in s.texts[0]
    run(go())
