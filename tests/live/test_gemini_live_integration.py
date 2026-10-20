"""OPT-IN real Gemini Live test. Never runs by default.

Run:  TARA_LIVE_TEST=1 GEMINI_API_KEY=... TARA_LIVE_MODEL=<live model id> \
      .venv/bin/python -m pytest -m live_api tests/live -q

It opens one real session, sends one short text turn and expects audio back. It makes a billable API call
(Live models have a free tier per the pricing page; check your plan). It prints no key and no transcripts.
"""
import os

import pytest

from tara.voice.smoke import run_smoke

pytestmark = pytest.mark.live_api

ENABLED = os.environ.get("TARA_LIVE_TEST") == "1" and os.environ.get("GEMINI_API_KEY") and os.environ.get("TARA_LIVE_MODEL")


@pytest.mark.skipif(not ENABLED, reason="set TARA_LIVE_TEST=1, GEMINI_API_KEY and TARA_LIVE_MODEL to run against the real API")
def test_real_session_returns_audio_and_completes_a_turn():
    import asyncio
    rep = asyncio.run(run_smoke(timeout_s=60))
    assert rep["audio_bytes"] > 0 and rep["turn_complete"] is True
    assert rep["sample_rate"] == 24000          # docs: output is always 24 kHz PCM16
    assert rep["connect_ms"] > 0
