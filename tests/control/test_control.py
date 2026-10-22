"""Spoken commands as real, permission-checked application actions."""
import asyncio

from tara.adapters.local import LocalMeetingAdapter
from tara.control.commands import CommandKind, parse_command
from tara.control.policy import authorize
from tara.control.state import ControlState
from tara.core.config import SpeakingPolicy, validate
from tara.core.contracts import AdapterCapabilities, PCMChunk
from tara.core.metrics import Metrics
from tara.floor.audio import silence, tone
from tara.floor.classify import RuleIntentClassifier
from tara.floor.participation import Attribution
from tara.testing.voice_fakes import FakeVoiceProvider, FakeVoiceSession
from tara.voice.engine import LiveParams, LiveVoiceEngine
from tara.voice.events import Disconnected, InputTranscript, ResponseAudio, TurnComplete, VoiceSessionConfig
from tara.voice.memory import Scope
from tara.voice.participation import LiveParticipation
from tara.voice.registry import SessionRegistry
from tests.helpers import POLICY, ROLES, TH, cfg, group_cfg

run = asyncio.run
K = CommandKind
NAMES = {"a": "Candidate A", "b": "Candidate B", "r": "Recruiter"}
CH = b"\x00\x00" * 2400


def kind(text):
    c = parse_command(text)
    return c.kind if c else None


def test_grammar_requires_addressing_tara_and_is_precise():
    cases = {
        "Tara, mute your microphone": K.MIC_MUTE, "Tara mute yourself please": K.MIC_MUTE,
        "Tara, stay quiet for a while but keep listening": K.LISTEN_ONLY, "Tara, just listen for now": K.LISTEN_ONLY,
        "Tara, you can speak again": K.RESUME, "Tara, unmute yourself": K.RESUME,
        "Tara, leave the meeting": K.LEAVE, "Tara, you can drop off the call": K.LEAVE,
        "Tara, turn your camera on": K.CAMERA_ON, "Tara, camera off": K.CAMERA_OFF,
        "Tara, can we see you?": K.SHOW_IDENTITY, "Tara, what do you look like": K.SHOW_IDENTITY,
        "Tara, take notes on this discussion": K.NOTES_ON, "Tara, stop taking notes": K.NOTES_OFF,
        "Tara, confirm that": K.CONFIRM_NOTE, "Tara, mute Bob": K.MUTE_OTHER,
    }
    for text, expected in cases.items():
        assert kind(text) is expected, text
    for text in ("we should mute alerts during the deploy", "Alex, please leave the meeting early", "can we see the dashboard",
                 "I will take notes on this", "the camera on that laptop is broken", "Sara, mute your mic",
                 "yesterday Tara said she would leave the meeting", "stay quiet while I think"):
        assert kind(text) is None, text
    assert parse_command("Tara, mute Bob").target == "bob"
    assert kind("Tara, mute your microphone and leave the meeting") is K.MIC_MUTE      # first matching intent only


def test_command_names_match_config_names():
    from tara.core.config import COMMAND_NAMES, KIND_NAMES
    assert COMMAND_NAMES == {k.value for k in CommandKind}
    from tara.behaviour.meeting_kind import MeetingKind
    assert KIND_NAMES == {k.value for k in MeetingKind}


CAPS_ALL = AdapterCapabilities(can_mute_self=True, can_camera=True, can_show_image=True)
R_ = Attribution("r", 0.99)
A_ = Attribution("a", 0.99)
U_ = Attribution(None, 0.0)


def auth(cmd, c=None, att=R_, state=None, caps=CAPS_ALL, identity=True):
    return authorize(parse_command(cmd), c or cfg(), ROLES, att, 0.8, state or ControlState(), caps, identity)


def test_permission_matrix_easy_to_silence_hard_to_expand():
    assert auth("Tara, be quiet", att=U_).ok                                     # anyone can silence her, even unknown speakers
    assert auth("Tara, mute your microphone", att=A_).ok
    assert not auth("Tara, leave the meeting", att=A_).ok and auth("Tara, leave the meeting", att=A_).reason == "needs_control_role"
    assert auth("Tara, leave the meeting", att=U_).reason == "identity_low"
    assert auth("Tara, leave the meeting", att=R_).ok
    assert auth("Tara, turn your camera on", att=R_).ok and not auth("Tara, turn your camera on", att=A_).ok
    assert auth("Tara, take notes", att=R_).ok and not auth("Tara, take notes", att=A_).ok
    assert not auth("Tara, leave the meeting", att=Attribution("r", 0.4)).ok              # control role but unreliable identity
    assert not auth("Tara, leave the meeting", att=Attribution("r", 0.99, overlap=True)).ok


def test_resume_needs_a_control_role_or_the_person_who_silenced_her():
    s = ControlState(speech_mode="listen_only", set_by="a")
    assert auth("Tara, you can speak again", att=R_, state=s).ok
    ok = auth("Tara, you can speak again", att=A_, state=s)
    assert ok.ok and ok.reason == "muter_may_resume"
    assert not auth("Tara, you can speak again", att=Attribution("b", 0.99), state=s).ok
    assert auth("Tara, you can speak again", att=U_, state=s).reason == "identity_low"


def test_interviews_stop_candidates_from_silencing_the_interviewer():
    g = group_cfg()
    assert not auth("Tara, be quiet", c=g, att=A_).ok
    assert auth("Tara, be quiet", c=g, att=R_).ok


def test_platform_reality_and_defaults():
    assert auth("Tara, turn your camera on", caps=AdapterCapabilities()).reason == "unsupported"
    assert auth("Tara, can we see you", caps=CAPS_ALL, identity=False).reason == "unsupported"
    assert auth("Tara, can we see you", caps=CAPS_ALL, identity=True).ok
    assert auth("Tara, mute Bob", caps=CAPS_ALL).reason == "not_enabled"                    # off unless listed explicitly
    assert auth("Tara, mute Bob", c=cfg(allowed_commands=("mute_other",)), caps=CAPS_ALL).reason == "unsupported"
    assert auth("Tara, mute Bob", c=cfg(allowed_commands=("mute_other",)),
                caps=AdapterCapabilities(can_mute_others=True)).ok
    assert auth("Tara, leave the meeting", c=cfg(allowed_commands=("listen_only",))).reason == "not_enabled"


def test_config_validates_command_and_kind_lists():
    assert validate(cfg(allowed_commands=("leave", "listen_only")), POLICY).ok
    assert not validate(cfg(allowed_commands=("fly",)), POLICY).ok
    assert any(i.severity == "warning" for i in validate(cfg(allowed_commands=("mute_other",)), POLICY).issues)
    assert not validate(cfg(meeting_kind="party"), POLICY).ok and validate(cfg(meeting_kind="technical"), POLICY).ok


# ---- engine ---------------------------------------------------------------------------------------------------------------------------
async def until(cond, timeout=3.0):
    end = asyncio.get_event_loop().time() + timeout
    while not cond():
        if asyncio.get_event_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


def audio(rid, n=1):
    return [ResponseAudio(rid, CH) for _ in range(n)]


def mic(ms, stream):
    pcm = tone(ms)
    return [PCMChunk(pcm[i:i + 640], 16000, i // 32, stream) for i in range(0, len(pcm), 640)]


def hush(stream, t0=3000):
    return [PCMChunk(silence(500), 16000, t0, stream)]


def mk(c=None, controls=True, fail_controls=False, identity="/tmp/tara.png", params=None, factor=0.1, sessions=None):
    c = c or cfg()
    ad = LocalMeetingAdapter(realtime_factor=factor, controls=controls, fail_controls=fail_controls)
    sessions = sessions or [FakeVoiceSession()]
    s = sessions[0]
    eng = LiveVoiceEngine(
        session_id="s1", scope=Scope("t", "m"), cfg=c, roles=ROLES, names=NAMES,
        provider=FakeVoiceProvider(list(sessions)),
        session_config=VoiceSessionConfig("sys"), adapter=ad,
        participation=LiveParticipation(c, ROLES, RuleIntentClassifier(), TH, caps=ad.capabilities, identity_available=bool(identity)),
        registry=SessionRegistry(2), metrics=Metrics(), identity_image=identity,
        params=params or LiveParams(hold_ms=300, floor_wait_ms=300, leave_grace_s=0.3, guidance_injection=False))
    return eng, s, ad


async def speak_as(eng, stream, ms=300):
    for ch in mic(ms, stream) + hush(stream, 4000):
        await eng.on_meeting_audio(ch)


def test_listen_only_is_persistent_stops_speech_and_blocks_everything_until_resumed():
    async def go():
        eng, s, ad = mk(factor=1.0)
        await eng.start()
        try:
            s.push(InputTranscript("Tara, explain the plan", True), *audio(1, 10))
            await until(lambda: eng.playback.is_playing)
            await asyncio.sleep(0.2)
            s.push(InputTranscript("Tara, stay quiet for a while but keep listening", True))
            await until(lambda: eng.control.speech_mode == "listen_only")
            assert not eng.playback.is_playing and ad.completed["live_1"] is False
            assert s.texts == []                                               # silence is the confirmation: nothing is said
            s.push(TurnComplete(1))
            for rid, text in ((2, "Tara, what do you think?"), (3, "Tara, please answer this one"), (4, "great point, haha")):
                s.push(InputTranscript(text, True), *audio(rid, 2), TurnComplete(rid))
            await until(lambda: eng.metrics.counters["gate_denied"] >= 3)
            assert len(eng.playback.results) == 1                              # still only the interrupted first reply
            assert eng.status == "active" and eng.metrics.counters["control.listen_only"] == 1
            await eng.on_meeting_audio(PCMChunk(b"\x01\x00" * 160, 16000, 0, "mix"))
            await until(lambda: len(s.audio) >= 1)                              # still listening: audio keeps reaching the model
        finally:
            await eng.close()
    run(go())


def test_listen_only_survives_provider_turns_app_prompts_and_reconnect_until_resumed():
    async def go():
        s1, s2 = FakeVoiceSession(), FakeVoiceSession()
        eng, s, ad = mk(sessions=[s1, s2])
        await eng.start()
        try:
            await speak_as(eng, "a")
            s.push(InputTranscript("Tara, stay quiet for a while but keep listening", True))
            await until(lambda: eng.control.speech_mode == "listen_only")
            for rid, text in ((2, "Tara, what do you think?"), (3, "Tara, answer this one"), (4, "great point, haha")):
                s.push(InputTranscript(text, True), *audio(rid, 2), TurnComplete(rid))
            await eng.prompt("Ask the next question.")                   # app-initiated speech is still silenced
            s.push(*audio(5, 2), TurnComplete(5))
            await until(lambda: eng.metrics.counters["gate_denied"] >= 4)
            assert eng.playback.results == []

            s.push(Disconnected("network", True))                        # reconnect must not clear the state
            await until(lambda: eng.metrics.counters["live_reconnects"] == 1)
            assert eng.control.speech_mode == "listen_only"
            s2.push(InputTranscript("Tara, are you back?", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: eng.metrics.counters["gate_denied"] >= 5)
            assert eng.playback.results == []

            await speak_as(eng, "r")                                     # explicit, authorized cancel
            s2.push(InputTranscript("Tara, you can speak again", True))
            await until(lambda: eng.control.speech_mode == "active")
            s2.push(InputTranscript("Tara, hello", True), *audio(2, 1), TurnComplete(2))
            await until(lambda: eng.playback.results)
        finally:
            await eng.close()
    run(go())


def test_proactive_policy_cannot_break_listen_only():
    async def go():
        eng, s, ad = mk(c=cfg(policy=SpeakingPolicy.PROACTIVE_ALLOWED))
        await eng.start()
        try:
            s.push(InputTranscript("Tara, be quiet", True))
            await until(lambda: eng.muted)
            s.push(InputTranscript("interesting, we should add caching", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: eng.metrics.counters["gate_denied"] >= 1)
            assert eng.playback.results == []
        finally:
            await eng.close()
    run(go())


def test_resume_by_control_role_restores_speech_and_confirms_naturally():
    async def go():
        eng, s, ad = mk()
        await eng.start()
        try:
            await speak_as(eng, "a")
            s.push(InputTranscript("Tara, be quiet", True))
            await until(lambda: eng.muted)
            assert eng.control.set_by is None or eng.control.speech_mode == "listen_only"
            await speak_as(eng, "b")                                            # a candidate tries to bring her back
            s.push(InputTranscript("Tara, you can speak again", True))
            await asyncio.sleep(0.1)
            assert eng.muted                                                    # not authorized (not the muter, not a control role)
            await speak_as(eng, "r")
            s.push(InputTranscript("Tara, you can speak again", True))
            await until(lambda: not eng.muted)
            await until(lambda: s.texts)
            assert "may speak again" in s.texts[0] and s.texts[0].startswith("[Application message")
            s.push(*audio(5, 2), TurnComplete(5))                               # the confirmation is app-initiated, so it plays
            await until(lambda: eng.playback.results)
        finally:
            await eng.close()
    run(go())


def test_the_person_who_silenced_tara_can_resume_her():
    async def go():
        eng, s, ad = mk()
        await eng.start()
        try:
            await speak_as(eng, "a")
            s.push(InputTranscript("Tara, hold off for now", True))
            await until(lambda: eng.muted)
            assert eng.control.set_by == "a"
            await speak_as(eng, "a")
            s.push(InputTranscript("Tara, you can speak again", True))
            await until(lambda: not eng.muted)
        finally:
            await eng.close()
    run(go())


def test_mic_mute_uses_the_platform_when_supported_and_falls_back_to_app_silence_when_it_fails():
    async def go():
        eng, s, ad = mk()
        await eng.start()
        try:
            s.push(InputTranscript("Tara, mute your microphone", True))
            await until(lambda: eng.control.speech_mode == "muted")
            assert ("set_microphone", False) in ad.calls
            await speak_as(eng, "r")
            s.push(InputTranscript("Tara, unmute yourself", True))
            await until(lambda: not eng.muted)
            assert ("set_microphone", True) in ad.calls
        finally:
            await eng.close()
        eng2, s2, ad2 = mk(fail_controls=True)
        await eng2.start()
        try:
            s2.push(InputTranscript("Tara, mute your microphone", True))
            await until(lambda: eng2.control.speech_mode == "muted")
            await until(lambda: eng2.metrics.counters["control_platform_failed"] == 1)
            s2.push(InputTranscript("Tara, hello", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: eng2.metrics.counters["gate_denied"] >= 1)
            assert eng2.playback.results == []                                   # the app-level silence holds regardless
        finally:
            await eng2.close()
        eng3, s3, ad3 = mk(controls=False)                                       # adapter cannot mute: app-level silence only
        await eng3.start()
        try:
            s3.push(InputTranscript("Tara, mute your microphone", True))
            await until(lambda: eng3.muted)
            assert ad3.calls == []
        finally:
            await eng3.close()
    run(go())


def test_leave_requires_a_control_role_says_goodbye_then_really_leaves():
    async def go():
        eng, s, ad = mk()
        await eng.start()
        try:
            await speak_as(eng, "a")
            s.push(InputTranscript("Tara, leave the meeting", True))
            await until(lambda: eng.metrics.counters["control_denied.leave.needs_control_role"] == 1)
            assert eng.status == "active" and ("leave", None) not in ad.calls
            await until(lambda: s.texts)
            assert "leave that to the organizer" in s.texts[0]
        finally:
            pass
        await speak_as(eng, "r")
        s.push(InputTranscript("Tara, leave the meeting", True))
        await until(lambda: any("leaving the meeting" in t for t in s.texts))
        await asyncio.wait_for(eng.wait_closed(), 3)
        assert ("leave", None) in ad.calls and eng.control.left and eng.status == "closed"
    run(go())


def test_camera_and_picture_are_honest_about_what_the_platform_can_do():
    async def go():
        eng, s, ad = mk()
        await eng.start()
        try:
            await speak_as(eng, "r")
            s.push(InputTranscript("Tara, turn your camera on", True))
            await until(lambda: eng.control.camera_on is True)
            assert ("set_camera", True) in ad.calls and any("camera is now on" in t for t in s.texts)
            await speak_as(eng, "a")
            s.push(InputTranscript("Tara, can we see you?", True))
            await until(lambda: eng.control.identity_shown)
            assert ("show_image", "/tmp/tara.png") in ad.calls and any("showing your picture" in t for t in s.texts)
        finally:
            await eng.close()
        eng2, s2, ad2 = mk(controls=False)                                          # platform cannot: tell the truth, do nothing
        await eng2.start()
        try:
            await speak_as(eng2, "r")
            s2.push(InputTranscript("Tara, can we see you?", True))
            await until(lambda: s2.texts)
            assert "can't do that here" in s2.texts[0] and ad2.calls == [] and not eng2.control.identity_shown
        finally:
            await eng2.close()
        eng3, s3, ad3 = mk(fail_controls=True)
        await eng3.start()
        try:
            await speak_as(eng3, "r")
            s3.push(InputTranscript("Tara, turn your camera on", True))
            await until(lambda: s3.texts)
            assert "could not change the camera" in s3.texts[0] and eng3.control.camera_on is None
        finally:
            await eng3.close()
    run(go())


def test_commands_never_get_answered_as_questions_and_never_fire_when_merely_discussed():
    async def go():
        eng, s, ad = mk()
        await eng.start()
        try:
            s.push(InputTranscript("we should probably mute the alerts and leave the meeting early", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: eng.metrics.counters["gate_denied"] == 1)
            assert not eng.muted and eng.status == "active" and ad.calls == []
            await speak_as(eng, "a")
            s.push(InputTranscript("Tara, leave the meeting", True), *audio(2, 2), TurnComplete(2))     # denied command: the model's reply is dropped
            await until(lambda: eng.metrics.counters["control_denied.leave.needs_control_role"] == 1)
            assert eng.playback.results == []
        finally:
            await eng.close()
    run(go())


def test_commands_survive_a_reconnect_and_takeover_still_works_alongside():
    async def go():
        s1, s2 = FakeVoiceSession(), FakeVoiceSession()
        c = cfg()
        ad = LocalMeetingAdapter(controls=True)
        eng = LiveVoiceEngine(session_id="s1", scope=Scope("t", "m"), cfg=c, roles=ROLES, names=NAMES,
                              provider=FakeVoiceProvider([s1, s2]), session_config=VoiceSessionConfig("sys"), adapter=ad,
                              participation=LiveParticipation(c, ROLES, RuleIntentClassifier(), TH, caps=ad.capabilities),
                              registry=SessionRegistry(2), metrics=Metrics(),
                              params=LiveParams(hold_ms=300, reconnect_backoff_s=0.01, guidance_injection=False))
        await eng.start()
        try:
            s1.push(InputTranscript("Tara, be quiet", True))
            await until(lambda: eng.muted)
            from tara.voice.events import Disconnected
            s1.push(Disconnected("net", True))
            await until(lambda: eng.metrics.counters["live_reconnects"] == 1)
            assert eng.muted                                                     # silence is app state, not session state
            s2.push(InputTranscript("Tara, hello", True), *audio(1, 2), TurnComplete(1))
            await until(lambda: eng.metrics.counters["gate_denied"] >= 1)
            assert eng.playback.results == []
        finally:
            await eng.close()
    run(go())
