"""Gemini Live adapter: message mapping, config building, connection handling, redaction.
Uses real google-genai TYPES (installed) but never any network."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest

from tara.errors import FatalProviderError, ProviderError, ProviderTimeout
from tara.voice.events import (
    Disconnected,
    GenerationComplete,
    GoAway,
    InputTranscript,
    Interrupted,
    OutputTranscript,
    ResponseAudio,
    ResumptionHandle,
    SessionReady,
    ToolCallRequested,
    ToolCallsCancelled,
    ToolDecl,
    TurnComplete,
    VoiceSessionConfig,
)
from tara.voice.gemini_live import GeminiLiveProvider, ResponseTracker, _Queue, build_live_config, map_message, redact, safe_error

run = asyncio.run
SECRET = "AIza-TOP-SECRET-KEY"


def audio_msg(data=b"\x01\x00" * 10, mime="audio/pcm;rate=24000"):
    return NS(setup_complete=None, tool_call=None, tool_call_cancellation=None, go_away=None, session_resumption_update=None,
              server_content=NS(input_transcription=None, output_transcription=None, interrupted=None, generation_complete=None,
                                turn_complete=None, model_turn=NS(parts=[NS(inline_data=NS(data=data, mime_type=mime))])))


def content_msg(**kw):
    base = dict(input_transcription=None, output_transcription=None, interrupted=None, generation_complete=None,
                turn_complete=None, model_turn=None, turn_complete_reason=None)
    base.update(kw)
    return NS(setup_complete=None, tool_call=None, tool_call_cancellation=None, go_away=None,
              session_resumption_update=None, server_content=NS(**base))


def test_audio_turn_and_response_numbering():
    tr = ResponseTracker()
    a1 = map_message(audio_msg(), tr)
    a2 = map_message(audio_msg(), tr)
    assert [type(e) for e in a1 + a2] == [ResponseAudio, ResponseAudio]
    assert a1[0].response_id == a2[0].response_id == 1 and a1[0].sample_rate == 24000
    done = map_message(content_msg(turn_complete=True), tr)
    assert done == [TurnComplete(1, "")]
    assert map_message(audio_msg(), tr)[0].response_id == 2            # new turn, new id


def test_interruption_closes_the_response_and_next_audio_is_new():
    tr = ResponseTracker()
    map_message(audio_msg(), tr)
    ev = map_message(content_msg(interrupted=True), tr)
    assert ev == [Interrupted(1)]
    assert map_message(audio_msg(), tr)[0].response_id == 2


def test_transcripts_generation_complete_and_sample_rate_parsing():
    tr = ResponseTracker()
    ev = map_message(content_msg(input_transcription=NS(text="hello tara", finished=True),
                                 output_transcription=NS(text="hi", finished=False), generation_complete=True), tr)
    assert ev == [InputTranscript("hello tara", True), OutputTranscript("hi", 0, False), GenerationComplete(0)]
    assert map_message(audio_msg(mime="audio/pcm;rate=16000"), ResponseTracker())[0].sample_rate == 16000
    assert map_message(audio_msg(mime=None), ResponseTracker())[0].sample_rate == 24000
    assert map_message(audio_msg(data=b""), ResponseTracker()) == []


def test_tool_events_goaway_resumption_setup():
    tr = ResponseTracker()
    m = NS(setup_complete=NS(), server_content=None,
           tool_call=NS(function_calls=[NS(id="c1", name="search_knowledge", args={"query": "x"}), NS(id="c2", name="t", args=None)]),
           tool_call_cancellation=NS(ids=["c9"]), go_away=NS(time_left=timedelta(seconds=7)),
           session_resumption_update=NS(new_handle="h1", resumable=True))
    ev = map_message(m, tr)
    kinds = [type(e) for e in ev]
    assert kinds == [SessionReady, ToolCallRequested, ToolCallsCancelled, GoAway, ResumptionHandle]
    assert ev[1].calls[0].args == {"query": "x"} and ev[1].calls[1].args == {} and ev[2].call_ids == ("c9",)
    assert ev[3].time_left_s == 7.0 and ev[4].handle == "h1"
    assert map_message(NS(setup_complete=None, server_content=None, tool_call=None, tool_call_cancellation=None,
                          go_away=NS(time_left="3s"), session_resumption_update=NS(new_handle=None, resumable=False)), tr) == [GoAway(3.0)]


def test_mapper_works_on_real_sdk_message_types():
    types = pytest.importorskip("google.genai.types")
    msg = types.LiveServerMessage(server_content=types.LiveServerContent(
        model_turn=types.Content(role="model", parts=[types.Part(inline_data=types.Blob(data=b"\x00\x01" * 4, mime_type="audio/pcm;rate=24000"))]),
        input_transcription=types.Transcription(text="hi", finished=True)))
    ev = map_message(msg, ResponseTracker())
    assert [type(e) for e in ev] == [InputTranscript, ResponseAudio] and ev[1].pcm == b"\x00\x01" * 4
    done = map_message(types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True)), ResponseTracker())
    assert done == [Interrupted(0)]
    tc = types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(id="1", name="n", args={"a": 1})]))
    assert map_message(tc, ResponseTracker())[0].calls[0].args == {"a": 1}


def test_build_live_config_with_real_sdk_types():
    types = pytest.importorskip("google.genai.types")
    cfg = VoiceSessionConfig("be brief", voice_name="Kore", tools=(ToolDecl("t", "d", {"type": "OBJECT", "properties": {}}),),
                             silence_duration_ms=600, activity_handling="none", proactive_audio=True, tool_behavior="non_blocking")
    lc = build_live_config(cfg, "handle-1", types)
    assert lc.response_modalities == ["AUDIO"] and lc.system_instruction == "be brief"
    assert lc.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    assert lc.input_audio_transcription is not None and lc.output_audio_transcription is not None
    assert lc.realtime_input_config.activity_handling == types.ActivityHandling.NO_INTERRUPTION
    assert lc.realtime_input_config.automatic_activity_detection.silence_duration_ms == 600
    assert lc.session_resumption.handle == "handle-1" and lc.context_window_compression is not None
    assert lc.proactivity.proactive_audio is True
    decl = lc.tools[0].function_declarations[0]
    assert decl.name == "t" and decl.behavior == types.Behavior.NON_BLOCKING
    plain = build_live_config(VoiceSessionConfig("x", resumption=False, context_compression=False, input_transcription=False,
                                                 output_transcription=False), None, types)
    assert plain.session_resumption is None and plain.context_window_compression is None and plain.tools is None
    assert plain.realtime_input_config.activity_handling == types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS


def test_redaction_and_safe_error():
    assert redact(f"bad key {SECRET} here", (SECRET,)) == "bad key [REDACTED] here"
    e = RuntimeError(f"url?key={SECRET}")
    e.code = 403
    s = safe_error(e, (SECRET,))
    assert SECRET not in s and "403" in s and s.startswith("RuntimeError")
    assert len(safe_error(RuntimeError("x" * 1000), ())) < 200


# ---- fake SDK ---------------------------------------------------------------------------------------------
class FakeSdkSession:
    def __init__(self, turns=None, raise_after=None):
        self.turns = list(turns or [])
        self.raise_after = raise_after
        self.sent = []
        self.closed = False

    async def receive(self):
        if self.turns:
            for m in self.turns.pop(0):
                yield m
            return
        if self.raise_after is not None:
            raise self.raise_after
        await asyncio.sleep(3600)
        yield  # pragma: no cover

    async def send_realtime_input(self, **kw):
        self.sent.append(("rt", kw))

    async def send_client_content(self, **kw):
        self.sent.append(("cc", kw))

    async def send_tool_response(self, **kw):
        self.sent.append(("tr", kw))


class FakeConnect:
    def __init__(self, sess, error=None, delay=0.0):
        self.sess, self.error, self.delay = sess, error, delay

    async def __aenter__(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.sess

    async def __aexit__(self, *a):
        self.sess.closed = True


class FakeGenai:
    last = None

    class Client:
        def __init__(self, **kw):
            FakeGenai.last = kw
            self.aio = NS(live=NS(connect=lambda **k: FakeGenai.next_connect))

    next_connect = None


def provider(connect, **kw):
    types = pytest.importorskip("google.genai.types")
    FakeGenai.next_connect = connect
    return GeminiLiveProvider(SECRET, "model-x", sdk_loader=lambda: (FakeGenai, types), **kw)


def test_session_streams_events_and_sends_with_correct_sdk_calls():
    pytest.importorskip("google.genai.types")

    async def go():
        sdk = FakeSdkSession(turns=[[audio_msg(), content_msg(turn_complete=True)]], raise_after=ConnectionError("gone"))
        s = await provider(FakeConnect(sdk)).open(VoiceSessionConfig("sys"))
        evs = []
        async for e in s.events():
            evs.append(e)
        assert isinstance(evs[0], SessionReady) and evs[0].connect_ms >= 0
        assert [type(e) for e in evs[1:3]] == [ResponseAudio, TurnComplete]
        assert isinstance(evs[-1], Disconnected) and evs[-1].recoverable
        await s.send_audio(b"\x00\x00")
        await s.end_audio_stream()
        await s.send_text("hi")
        await s.send_context("earlier we agreed X")
        await s.send_tool_result("c1", "t", {"result": 1})
        kinds = [k for k, _ in sdk.sent]
        assert kinds == ["rt", "rt", "rt", "cc", "tr"]
        blob = sdk.sent[0][1]["audio"]
        assert blob.mime_type == "audio/pcm;rate=16000" and blob.data == b"\x00\x00"
        assert sdk.sent[1][1] == {"audio_stream_end": True} and sdk.sent[2][1] == {"text": "hi"}
        assert sdk.sent[3][1]["turn_complete"] is False
        fr = sdk.sent[4][1]["function_responses"][0]
        assert (fr.id, fr.name, fr.response) == ("c1", "t", {"result": 1})
        await s.close(); await s.close()
        assert sdk.closed
    run(go())


def test_connect_timeout_and_error_mapping_never_leak_the_key():
    pytest.importorskip("google.genai.types")

    async def go():
        with pytest.raises(ProviderTimeout):
            await provider(FakeConnect(FakeSdkSession(), delay=1), connect_timeout_s=0.05).open(VoiceSessionConfig("s"))
        err = RuntimeError(f"rejected key={SECRET}")
        err.code = 401
        with pytest.raises(FatalProviderError) as e:
            await provider(FakeConnect(FakeSdkSession(), error=err)).open(VoiceSessionConfig("s"))
        assert SECRET not in str(e.value) and "401" in str(e.value)
        flaky = RuntimeError("socket reset")
        with pytest.raises(ProviderError) as e2:
            await provider(FakeConnect(FakeSdkSession(), error=flaky)).open(VoiceSessionConfig("s"))
        assert not isinstance(e2.value, FatalProviderError)
    run(go())


def test_fatal_vs_recoverable_disconnect_classification():
    pytest.importorskip("google.genai.types")

    async def go():
        for code, recoverable in ((403, False), (1011, True)):
            err = RuntimeError(f"bye {SECRET}")
            err.code = code
            s = await provider(FakeConnect(FakeSdkSession(raise_after=err))).open(VoiceSessionConfig("s"))
            last = [e async for e in s.events()][-1]
            assert isinstance(last, Disconnected) and last.recoverable is recoverable and SECRET not in last.reason
            await s.close()
    run(go())


def test_api_version_only_when_needed_and_validation():
    pytest.importorskip("google.genai.types")

    async def go():
        s = await provider(FakeConnect(FakeSdkSession())).open(VoiceSessionConfig("s"))
        assert "http_options" not in FakeGenai.last and FakeGenai.last["api_key"] == SECRET
        await s.close()
        s = await provider(FakeConnect(FakeSdkSession())).open(VoiceSessionConfig("s", proactive_audio=True))
        assert FakeGenai.last["http_options"] == {"api_version": "v1beta"}
        await s.close()
    run(go())
    with pytest.raises(ValueError):
        GeminiLiveProvider("", "m")
    with pytest.raises(ValueError):
        GeminiLiveProvider("k", "")
    assert SECRET not in repr(GeminiLiveProvider(SECRET, "m"))


def test_missing_sdk_is_a_clear_error():
    import tara.voice.gemini_live as g

    def boom():
        raise ProviderError("google-genai is not installed")
    with pytest.raises(ProviderError):
        run(GeminiLiveProvider("k", "m", sdk_loader=boom).open(VoiceSessionConfig("s")))
    assert g._load_sdk  # real loader exists


def test_bounded_queue_drops_oldest_audio_never_control_events():
    async def go():
        q = _Queue(3)
        await q.put(ResponseAudio(1, b"a"))
        await q.put(InputTranscript("x"))
        await q.put(ResponseAudio(1, b"b"))
        await q.put(TurnComplete(1))                    # full: oldest AUDIO is evicted
        items = []
        for _ in range(3):
            items.append(await q.get())
        assert [type(i) for i in items] == [InputTranscript, ResponseAudio, TurnComplete] and q.dropped_audio == 1
        assert items[1].pcm == b"b"
        await q.close()
        assert await q.get() is None
    run(go())
