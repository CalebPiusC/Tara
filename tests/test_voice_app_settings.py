import asyncio
import json

import pytest
from helpers import ROLES, cfg

from tara.adapters.local import LocalMeetingAdapter
from tara.errors import ConfigError
from tara.settings import RuntimeSettings, validate
from tara.voice.app import LiveTaraApp
from tara.voice.events import InputTranscript, ResponseAudio, TurnComplete
from tara.voice.fakes import FakeVoiceProvider, FakeVoiceSession
from tara.voice.memory import Scope

run = asyncio.run
LIVE = {"TARA_LIVE_ENABLED": "1", "GEMINI_API_KEY": "SECRET-123", "TARA_LIVE_MODEL": "some-live-model"}


def errs(env):
    return {i.field for i in validate(RuntimeSettings.from_env(env)) if i.severity == "error"}


def test_live_settings_validation_and_secret_handling():
    assert errs({"TARA_LIVE_ENABLED": "1"}) >= {"GEMINI_API_KEY", "TARA_LIVE_MODEL"}
    assert errs(LIVE) == set()
    assert errs({**LIVE, "TARA_LIVE_MAX_SESSIONS": "0", "TARA_LIVE_CONNECT_TIMEOUT_S": "0"}) == {"TARA_LIVE_MAX_SESSIONS", "TARA_LIVE_CONNECT_TIMEOUT_S"}
    s = RuntimeSettings.from_env(LIVE)
    assert "SECRET-123" not in repr(s) and "SECRET-123" not in json.dumps(s.redacted())
    assert s.live.context_compression and s.live.session_resumption and s.live.local_barge_in
    assert errs({}) == set()                                           # Live is opt-in; defaults stay valid without a key


def make(env=None, **kw):
    s = RuntimeSettings.from_env(env or LIVE)
    sess = FakeVoiceSession()
    ad = LocalMeetingAdapter(realtime_factor=0.1)
    app = LiveTaraApp(s, cfg(), ROLES, {"r": "Recruiter"}, ad, session_id="s1", scope=Scope("t", "m"),
                      provider=FakeVoiceProvider([sess]), **kw)
    return app, sess, ad


def test_app_refuses_to_build_without_live_enabled_or_key():
    with pytest.raises(ConfigError) as e:
        LiveTaraApp(RuntimeSettings(), cfg(), ROLES, {}, LocalMeetingAdapter(), session_id="x", scope=Scope("t", "m"))
    assert "TARA_LIVE_ENABLED" in str(e.value)
    with pytest.raises(ConfigError) as e2:
        make({"TARA_LIVE_ENABLED": "1", "GEMINI_API_KEY": "SECRET-123"})
    assert "TARA_LIVE_MODEL" in str(e2.value) and "SECRET-123" not in str(e2.value)


def test_app_runs_a_scripted_turn_end_to_end_with_no_local_models():
    async def go():
        app, sess, ad = make()
        await app.start()
        try:
            h = app.health()
            assert h["status"] == "active" and h["key"] == "set" and "SECRET-123" not in json.dumps(h)
            cfg_sent = app.provider.opened[0][0]
            assert "Tara" in cfg_sent.system_instruction and cfg_sent.context_compression
            assert [t.name for t in cfg_sent.tools] == ["record_note"]     # notes mode is on by default
            sess.push(InputTranscript("Tara, hello there", True), ResponseAudio(1, b"\x00\x00" * 2400), TurnComplete(1))
            end = asyncio.get_event_loop().time() + 3
            while not app.playback.results and asyncio.get_event_loop().time() < end:
                await asyncio.sleep(0.01)
            assert app.playback.results and app.playback.results[0].completed
        finally:
            await app.close()
        assert app.health()["status"] == "closed"
    run(go())
