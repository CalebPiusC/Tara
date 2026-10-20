"""Behaviour layer inside the Live engine: guidance notes, expression events, floor etiquette, and proof
that style never overrides gating, interruption, takeover or mute. Scripted provider: proves wiring and
policy, NOT how the live model sounds."""
import asyncio
import json

from helpers import ROLES, TH, cfg, group_cfg

from tara.adapters.local import LocalMeetingAdapter
from tara.audio import silence, tone
from tara.behaviour.controller import BehaviourController
from tara.behaviour.expressions import Expression, ExpressionEmitter, ListExpressionSink
from tara.behaviour.profiles import Profile, effective_profile
from tara.classify import RuleIntentClassifier
from tara.config import Mode, SpeakingPolicy
from tara.contracts import PCMChunk
from tara.metrics import Metrics
from tara.settings import RuntimeSettings
from tara.voice.app import LiveTaraApp
from tara.voice.engine import LiveParams, LiveVoiceEngine
from tara.voice.events import InputTranscript, Interrupted, OutputTranscript, ResponseAudio, TurnComplete, VoiceSessionConfig
from tara.voice.fakes import FakeVoiceProvider, FakeVoiceSession
from tara.voice.memory import Scope
from tara.voice.participation import LiveParticipation
from tara.voice.registry import SessionRegistry

run = asyncio.run
NAMES = {"a": "Candidate A", "b": "Candidate B", "r": "Recruiter"}
CH = b"\x00\x00" * 2400


def audio(rid, n=1):
    return [ResponseAudio(rid, CH) for _ in range(n)]


def mic(ms, stream, t0=0):
    pcm = tone(ms)
    return [PCMChunk(pcm[i:i + 640], 16000, t0 + i // 32, stream) for i in range(0, len(pcm), 640)]


def hush(stream, t0=3000):
    return [PCMChunk(silence(500), 16000, t0, stream)]


async def until(cond, timeout=3.0):
    end = asyncio.get_event_loop().time() + timeout
    while not cond():
        if asyncio.get_event_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


def mk(c=None, profile=Profile.CONVERSATIONAL, factor=0.1, params=None, sessions=None, guidance=True):
    c = c or cfg()
    prof = effective_profile(profile, c)
    sink = ListExpressionSink()
    adapter = LocalMeetingAdapter(realtime_factor=factor)
    sessions = sessions if sessions is not None else [FakeVoiceSession()]
    prov = FakeVoiceProvider(list(sessions))
    ctl = BehaviourController(prof, c.mode, guidance_min_interval_s=0.0)
    p = params or LiveParams(hold_ms=300, reconnect_backoff_s=0.01, guidance_injection=guidance, floor_wait_ms=400)
    eng = LiveVoiceEngine(
        session_id="s1", scope=Scope("t", "m"), cfg=c, roles=ROLES, names=NAMES, provider=prov,
        session_config=VoiceSessionConfig("sys"), adapter=adapter,
        participation=LiveParticipation(c, ROLES, RuleIntentClassifier(), TH), registry=SessionRegistry(2),
        params=p, metrics=Metrics(), behaviour=ctl, expressions=ExpressionEmitter(sink, "s1", emotion_min_interval_s=0.0))
    return eng, sessions, sink, ctl, adapter


def kinds(sink):
    return [e.kind for e in sink.events]


async def start(eng):
    await eng.start()
    return eng


# ---- expression events through a normal turn ----------------------------------------------------------------------
def test_expression_events_follow_the_turn_and_stay_context_supported():
    async def go():
        eng, (s,), sink, ctl, ad = mk(factor=0.1)
        await eng.start()
        try:
            for ch in mic(300, "a") + hush("a", 400):
                await eng.on_meeting_audio(ch)
            s.push(InputTranscript("Tara, what is the plan for the release?", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: Expression.SPEAKING_STOPPED in kinds(sink))
            ks = kinds(sink)
            assert ks.index(Expression.LISTENING) < ks.index(Expression.SPEAKING_STARTED) < ks.index(Expression.SPEAKING_STOPPED)
            assert not any(k in (Expression.AMUSED, Expression.SURPRISED, Expression.CONCERNED) for k in ks)   # no cue, no emotion
            assert ks[-1] is Expression.LISTENING
            assert all(e.session_id == "s1" for e in sink.events)
        finally:
            await eng.close()
    run(go())


def test_amused_only_with_a_joke_in_a_relaxed_room_and_concerned_for_bad_news():
    async def go():
        eng, (s,), sink, ctl, ad = mk(profile=Profile.CONVERSATIONAL)
        await eng.start()
        try:
            s.push(InputTranscript("haha Tara, that name is hilarious, what does it do?", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: Expression.SPEAKING_STOPPED in kinds(sink))
            amused = [e for e in sink.events if e.kind is Expression.AMUSED]
            assert len(amused) == 1 and amused[0].basis == "context" and amused[0].response_id == 1
            s.push(InputTranscript("Tara, unfortunately we lost the customer and I'm frustrated, any ideas?", True), *audio(2, 2), TurnComplete(2))
            await until(lambda: any(e.kind is Expression.CONCERNED for e in sink.events))
            assert not any(e.kind is Expression.AMUSED and e.response_id == 2 for e in sink.events)
        finally:
            await eng.close()
    run(go())


def test_interviews_emit_no_amusement_even_with_an_expressive_profile():
    async def go():
        eng, (s,), sink, ctl, ad = mk(c=group_cfg(), profile=Profile.EXPRESSIVE)
        await eng.start()
        try:
            for ch in mic(300, "a") + hush("a", 400):
                await eng.on_meeting_audio(ch)
            s.push(InputTranscript("haha Tara, funny, can you repeat the question?", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: Expression.SPEAKING_STOPPED in kinds(sink))
            assert Expression.AMUSED not in kinds(sink) and ctl.profile is Profile.PROFESSIONAL
        finally:
            await eng.close()
    run(go())


# ---- guidance to the live model ---------------------------------------------------------------------------------------------
def test_guidance_note_is_sent_when_the_mood_turns_serious_and_is_not_repeated():
    async def go():
        eng, (s,), sink, ctl, ad = mk()
        await eng.start()
        try:
            s.push(InputTranscript("the production outage is urgent and customers are affected", False))
            await until(lambda: s.contexts)
            note = s.contexts[0]
            assert "serious" in note and "Internal note" in note and "outage" not in note and "customers" not in note
            s.push(InputTranscript(" still urgent, the incident continues", False))
            await asyncio.sleep(0.1)
            assert len(s.contexts) == 1                                   # same situation: no repeat
            assert eng.metrics.counters["behaviour_guidance_sent"] == 1
            assert s.texts == [] and s.tool_results == []                 # a note is context, never a turn or a tool call
        finally:
            await eng.close()
    run(go())


def test_guidance_can_be_disabled_and_a_failing_send_never_breaks_the_session():
    async def go():
        eng, (s,), _, _, _ = mk(guidance=False)
        await eng.start()
        try:
            s.push(InputTranscript("urgent production outage", False))
            await asyncio.sleep(0.15)
            assert s.contexts == []
        finally:
            await eng.close()
        bad = FakeVoiceSession()

        async def boom(text):
            raise ConnectionError("send failed")
        bad.send_context = boom
        eng2, _, _, _, _ = mk(sessions=[bad])
        await eng2.start()
        try:
            bad.push(InputTranscript("urgent production outage", False))
            await until(lambda: eng2.metrics.counters["behaviour_guidance_errors"] == 1)
            assert eng2.status == "active"
        finally:
            await eng2.close()
    run(go())


def test_a_fresh_session_after_reconnect_gets_the_current_style_again():
    async def go():
        s1, s2 = FakeVoiceSession(), FakeVoiceSession()
        eng, _, _, _, _ = mk(sessions=[s1, s2])
        await eng.start()
        try:
            s1.push(InputTranscript("urgent production outage", False))
            await until(lambda: s1.contexts)
            s1.push(__import__("tara.voice.events", fromlist=["Disconnected"]).Disconnected("net", True))
            await until(lambda: eng.metrics.counters["live_reconnects"] == 1)
            assert any("serious" in c for c in s2.contexts)
        finally:
            await eng.close()
    run(go())


def test_output_checks_count_flags_without_logging_text_and_overlong_replies_trigger_brevity_guidance():
    async def go():
        eng, (s,), sink, ctl, ad = mk()
        await eng.start()
        try:
            long = "That's a great question! " + " ".join(["detail"] * 120)
            s.push(InputTranscript("Tara, what is the status of the migration plan", True), *audio(1, 2),
                   OutputTranscript(long, 1), TurnComplete(1))
            await until(lambda: eng.metrics.counters["behaviour_flag.overlong"] == 1)
            assert eng.metrics.counters["behaviour_flag.canned_opener"] == 1
            assert any("brief" in c for c in s.contexts)                   # feedback loop: shorter next time
            blob = json.dumps([(e.name, e.data) for e in eng.bus.history], default=str) + json.dumps(dict(eng.metrics.counters))
            assert "detail detail" not in blob and "great question" not in blob.lower()
        finally:
            await eng.close()
    run(go())


# ---- style never overrides participation, interruption, takeover or mute ------------------------------------------------------------
def test_jokes_and_expressive_profile_do_not_make_tara_speak_when_not_addressed():
    async def go():
        for prof in Profile:
            eng, (s,), sink, ctl, ad = mk(profile=prof)
            await eng.start()
            try:
                s.push(InputTranscript("haha that's hilarious, lol, we should ship that", True), *audio(1, 2), TurnComplete(1))
                await until(lambda e=eng: e.metrics.counters["gate_denied"] == 1)
                assert ad.sent == {} and eng.playback.results == [], prof
                assert Expression.SPEAKING_STARTED not in kinds(sink)
            finally:
                await eng.close()
    run(go())


def test_silent_policy_and_mute_beat_any_personality_setting():
    async def go():
        quiet = cfg(mode=Mode.OBSERVE, policy=SpeakingPolicy.SILENT)
        eng, (s,), sink, ctl, ad = mk(c=quiet, profile=Profile.EXPRESSIVE)
        await eng.start()
        try:
            s.push(InputTranscript("haha Tara, say something funny", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: eng.metrics.counters["gate_denied"] == 1)
            assert eng.playback.results == []
        finally:
            await eng.close()
        eng2, (s2,), sink2, ctl2, _ = mk(profile=Profile.EXPRESSIVE)
        await eng2.start()
        try:
            s2.push(InputTranscript("Tara, please be quiet for a bit", True), *audio(1, 1), TurnComplete(1))
            await until(lambda: eng2.muted)
            assert ctl2.muted
            s2.push(InputTranscript("haha Tara, tell us a joke", True), *audio(2, 2), TurnComplete(2))
            await until(lambda: eng2.metrics.counters["gate_denied"] >= 2)
            assert eng2.playback.results == [] and ctl2.last_plan.humour == "none"
            eng2.unmute()
            assert not ctl2.muted
        finally:
            await eng2.close()
    run(go())


def test_interruption_still_stops_speech_and_makes_the_next_answer_shorter():
    async def go():
        eng, (s,), sink, ctl, ad = mk(profile=Profile.EXPRESSIVE, factor=1.0)
        await eng.start()
        try:
            s.push(InputTranscript("Tara, give me the whole plan", True), *audio(1, 10))
            await until(lambda: eng.playback.is_playing)
            await asyncio.sleep(0.2)
            s.push(Interrupted(1))
            await until(lambda: eng.metrics.counters["live_interrupts.provider"] == 1)
            assert not eng.playback.is_playing and ad.completed["live_1"] is False and eng.status == "active"
            assert Expression.INTERRUPTED in kinds(sink)
            assert ctl.observe("so what should we do about the schema", final=False).verbosity == "brief"
            s.push(*audio(1, 3))                                          # stale chunks from the dead response
            await until(lambda: eng.metrics.counters["stale_audio_dropped"] == 3)
            ks = kinds(sink)
            assert ks.index(Expression.INTERRUPTED) < len(ks) and Expression.SPEAKING_STOPPED in ks
        finally:
            await eng.close()
    run(go())


def test_takeover_yields_and_the_behaviour_layer_stops_trying_to_reclaim():
    async def go():
        eng, (s,), sink, ctl, ad = mk(c=group_cfg(), factor=1.0)
        await eng.start()
        try:
            for ch in mic(300, "r") + hush("r", 400):
                await eng.on_meeting_audio(ch)
            s.push(InputTranscript("Thanks Tara, I'll take it from here", True), *audio(1, 3), TurnComplete(1))
            await until(lambda: eng.takeover_active)
            assert ctl.yielded and Expression.YIELDING_CONTROL in kinds(sink)
            assert ctl.observe("Tara what do you think", final=False).intent == "stay_back"
            s.push(InputTranscript("Tara, one more thing", True), *audio(2, 2), TurnComplete(2))
            await until(lambda: eng.metrics.counters["gate_denied"] >= 2)
            assert eng.playback.results == []
            eng.release_takeover()
            assert not ctl.yielded and kinds(sink)[-1] is Expression.LISTENING
        finally:
            await eng.close()
    run(go())


# ---- floor etiquette --------------------------------------------------------------------------------------------------------------------
def test_tara_waits_while_a_participant_is_mid_sentence_then_speaks():
    async def go():
        eng, (s,), sink, ctl, ad = mk(factor=0.1, params=LiveParams(hold_ms=300, floor_wait_ms=2000, guidance_injection=False))
        await eng.start()
        try:
            for ch in mic(300, "b"):                                       # someone else is talking and has not finished
                await eng.on_meeting_audio(ch)
            s.push(InputTranscript("Tara, what do you think?", True), *audio(1, 3), TurnComplete(1))
            await until(lambda: eng.metrics.counters["floor_waits"] == 1)
            await asyncio.sleep(0.15)
            assert ad.sent == {}                                           # not talking over them
            for ch in hush("b", 400):
                await eng.on_meeting_audio(ch)
            await until(lambda: eng.playback.results and eng.playback.results[0].completed)
        finally:
            await eng.close()
    run(go())


def test_if_the_floor_never_frees_the_moment_passes_and_tara_stays_quiet():
    async def go():
        eng, (s,), sink, ctl, ad = mk(params=LiveParams(hold_ms=300, floor_wait_ms=120, guidance_injection=False))
        await eng.start()
        try:
            for ch in mic(300, "b"):
                await eng.on_meeting_audio(ch)
            s.push(InputTranscript("Tara, what do you think?", True), *audio(1, 3), TurnComplete(1))
            await until(lambda: eng.metrics.counters["floor_wait_timeouts"] == 1)
            assert ad.sent == {} and eng.playback.results == []
        finally:
            await eng.close()
    run(go())


# ---- latency and cost -----------------------------------------------------------------------------------------------------------------------
def test_behaviour_adds_no_model_calls_no_delay_and_is_measured():
    async def go():
        eng, (s,), sink, ctl, ad = mk(factor=0.1)
        await eng.start()
        try:
            s.push(InputTranscript("Tara, what is the plan for release?", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: eng.playback.results)
            assert s.texts == [] and s.tool_results == []                  # nothing was asked of the model by the layer
            h = eng.metrics.summary("behaviour_ms")
            assert h["count"] >= 1 and h["p95"] < 50, h                    # lexical rules: milliseconds at most
            assert eng.metrics.summary("live_first_playback_ms")["count"] == 1
        finally:
            await eng.close()
    run(go())


def test_expression_sink_failure_cannot_disturb_playback():
    async def go():
        class Boom:
            def emit(self, ev):
                raise RuntimeError("avatar down")
        eng, (s,), _, _, ad = mk(factor=0.1)
        eng.expressions = ExpressionEmitter(Boom(), "s1")
        await eng.start()
        try:
            s.push(InputTranscript("Tara, hello", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: eng.playback.results and eng.playback.results[0].completed)
            assert eng.expressions.sink_errors >= 1
        finally:
            await eng.close()
    run(go())


# ---- composition: profile selection ---------------------------------------------------------------------------------------------------------
def make_app(c=None, env=None):
    s = RuntimeSettings.from_env({"TARA_LIVE_ENABLED": "1", "GEMINI_API_KEY": "k", "TARA_LIVE_MODEL": "m", **(env or {})})
    prov = FakeVoiceProvider([FakeVoiceSession()])
    app = LiveTaraApp(s, c or cfg(), ROLES, NAMES, LocalMeetingAdapter(), session_id="x", scope=Scope("t", "m"), provider=prov)
    return app, prov


def test_profile_selection_default_override_and_interview_clamp():
    app, prov = make_app()
    assert app.profile is Profile.PROFESSIONAL and app.health()["profile"] == "professional"
    app2, _ = make_app(env={"TARA_PERSONA_PROFILE": "conversational"})
    assert app2.profile is Profile.CONVERSATIONAL
    app3, _ = make_app(c=cfg(behaviour_profile="expressive"), env={"TARA_PERSONA_PROFILE": "conversational"})
    assert app3.profile is Profile.EXPRESSIVE                                # the meeting's request wins over the default
    app4, _ = make_app(c=group_cfg(behaviour_profile="expressive"))
    assert app4.profile is Profile.PROFESSIONAL                              # interviews are clamped
    assert "Personality:" in app3.engine.session_config.system_instruction
    assert "animated" in app3.engine.session_config.system_instruction.lower()
    assert "react briefly in your own voice" not in app3.engine.session_config.system_instruction
    app5, _ = make_app(env={"TARA_PERSONA_VOCAL_REACTIONS": "1", "TARA_PERSONA_PROFILE": "expressive"})
    assert "react briefly in your own voice" in app5.engine.session_config.system_instruction


def test_app_session_runs_with_the_behaviour_layer_and_default_professional_style():
    async def go():
        app, prov = make_app()
        await app.start()
        try:
            sess = app.engine._session
            sess.push(InputTranscript("Tara, hello there", True), *audio(1, 1), TurnComplete(1))
            await until(lambda: app.playback.results)
            assert app.engine.behaviour.profile is Profile.PROFESSIONAL
            assert app.metrics.summary("behaviour_ms")["count"] >= 1
        finally:
            await app.close()
    run(go())
