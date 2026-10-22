"""OPT-IN live behaviour evaluation against the real Gemini Live API. Never runs by default.

Run:  TARA_LIVE_TEST=1 GEMINI_API_KEY=... TARA_LIVE_MODEL=<live model id> \
      .venv/bin/python -m pytest -m live_api tests/live/test_behaviour_live.py -q -s

What it measures (text transcription of the model's speech + audio length), over a small fixed scenario set:
  canned-opener rate, spoken-list/stage-direction/AI-disclaimer/human-experience flags, length, and whether a
  mid-session style note triggers an unwanted spoken reply.
What it CANNOT measure: laughter, tone, pacing or any other vocal quality. Those need a human listening.
The model is stochastic: thresholds are loose on purpose and failures should be read, not silenced.
Prints counts and rates only; never the key and never transcripts.
"""
import asyncio
import os
import time

import pytest

from tara.behaviour.checks import flag_text
from tara.behaviour.persona import persona_instruction
from tara.behaviour.profiles import Profile
from tara.core.settings import RuntimeSettings
from tara.voice.events import OutputTranscript, ResponseAudio, TurnComplete, VoiceSessionConfig
from tara.voice.gemini_live import GeminiLiveProvider

pytestmark = pytest.mark.live_api
ENABLED = os.environ.get("TARA_LIVE_TEST") == "1" and os.environ.get("GEMINI_API_KEY") and os.environ.get("TARA_LIVE_MODEL")
SKIP = pytest.mark.skipif(not ENABLED, reason="set TARA_LIVE_TEST=1, GEMINI_API_KEY and TARA_LIVE_MODEL")

SCENARIOS = {
    "direct_technical": "Tara, how does an idempotency key stop duplicate payments when a client retries?",
    "joke": "Ha, someone just named our staging server 'definitely-not-production'. Tara, thoughts?",
    "bad_news": "Unfortunately we didn't get the contract and I'm pretty disappointed. Tara, any ideas?",
    "correction": "No, I meant the staging cluster, not production. Tara, can you redo that last suggestion?",
    "unknown_history": "Tara, what did we decide in last Tuesday's meeting about the cache?",
    "quick": "Tara, quickly, is Redis single threaded?",
}


async def one_turn(session, text, timeout=45.0):
    await session.send_text(text)
    out, audio_bytes, t0 = [], 0, time.monotonic()

    async def consume():
        nonlocal audio_bytes
        async for ev in session.events():
            if isinstance(ev, ResponseAudio):
                audio_bytes += len(ev.pcm)
            elif isinstance(ev, OutputTranscript):
                out.append(ev.text)
            elif isinstance(ev, TurnComplete):
                return
    await asyncio.wait_for(consume(), timeout)
    return " ".join(out).strip(), audio_bytes / 48000, time.monotonic() - t0     # 24 kHz PCM16 mono = 48000 bytes/s


async def run_profile(profile):
    s = RuntimeSettings.from_env({**os.environ, "TARA_LIVE_ENABLED": "1"})
    provider = GeminiLiveProvider(s.gemini.api_key, s.live.model, s.live.connect_timeout_s, s.live.api_version or None)
    cfg = VoiceSessionConfig(system_instruction="You are Tara, a participant in a team meeting.\n" + persona_instruction(profile),
                             voice_name=s.live.voice_name, input_transcription=False, output_transcription=True)
    results = {}
    for name, text in SCENARIOS.items():            # a fresh session per scenario keeps cases independent
        session = await provider.open(cfg)
        try:
            transcript, audio_s, wall_s = await one_turn(session, text)
        finally:
            await session.close()
        flags = {f.name for f in flag_text(transcript, "concise", question=text)}
        results[name] = {"words": len(transcript.split()), "audio_s": round(audio_s, 1), "wall_s": round(wall_s, 1), "flags": sorted(flags)}
    return results


@SKIP
@pytest.mark.parametrize("profile", [Profile.PROFESSIONAL, Profile.CONVERSATIONAL])
def test_model_replies_follow_the_persona_rubric(profile):
    results = asyncio.run(run_profile(profile))
    n = len(results)
    canned = sum("canned_opener" in r["flags"] for r in results.values()) / n
    longish = sum("overlong" in r["flags"] for r in results.values()) / n
    print(f"\nprofile={profile.value} canned_opener_rate={canned:.2f} overlong_rate={longish:.2f}")
    for name, r in results.items():
        print(f"  {name}: words={r['words']} audio_s={r['audio_s']} wall_s={r['wall_s']} flags={r['flags']}")
    all_flags = {f for r in results.values() for f in r["flags"]}
    assert "ai_disclaimer" not in all_flags and "human_experience_claim" not in all_flags
    assert "stage_direction" not in all_flags and "exaggerated_laughter" not in all_flags
    assert canned <= 0.34 and longish <= 0.34
    assert all(r["words"] > 0 for r in results.values()), "no transcript came back: check that output transcription works for this model"


@SKIP
def test_a_style_note_sent_mid_session_does_not_trigger_a_spoken_reply():
    async def go():
        s = RuntimeSettings.from_env({**os.environ, "TARA_LIVE_ENABLED": "1"})
        provider = GeminiLiveProvider(s.gemini.api_key, s.live.model, s.live.connect_timeout_s, s.live.api_version or None)
        cfg = VoiceSessionConfig(system_instruction="You are Tara, a participant in a team meeting.\n" + persona_instruction(Profile.PROFESSIONAL),
                                 input_transcription=False, output_transcription=True)
        session = await provider.open(cfg)
        try:
            await session.send_context("Style update for your next replies. Internal note: do not speak it and do not answer it: "
                                       "the mood is serious: no humour, be direct and brief.")
            heard = 0

            async def watch():
                nonlocal heard
                async for ev in session.events():
                    if isinstance(ev, ResponseAudio):
                        heard += len(ev.pcm)
            try:
                await asyncio.wait_for(watch(), 8)
            except TimeoutError:
                pass
            return heard
        finally:
            await session.close()
    heard = asyncio.run(go())
    print(f"\naudio bytes after a style note (expected 0): {heard}")
    assert heard == 0, "the model spoke in response to a context note: set TARA_PERSONA_GUIDANCE=0 and report it"
