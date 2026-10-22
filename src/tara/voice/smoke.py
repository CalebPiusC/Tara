"""Opt-in real-API smoke test: `python -m tara.voice.smoke` (needs GEMINI_API_KEY, TARA_LIVE_MODEL).

Connects, sends one text turn, waits for the model's audio and turn completion, prints ONLY
non-sensitive measurements (never the key, never transcripts). Exit 0 = a real audio reply arrived.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from ..core.settings import RuntimeSettings, validate
from .events import ResponseAudio, SessionReady, TurnComplete, VoiceSessionConfig
from .gemini_live import GeminiLiveProvider


async def run_smoke(timeout_s: float = 45.0) -> dict:
    s = RuntimeSettings.from_env(os.environ)
    if not s.live.enabled:
        s = RuntimeSettings.from_env({**os.environ, "TARA_LIVE_ENABLED": "1"})
    errs = [f"{i.field}: {i.message}" for i in validate(s) if i.severity == "error"]
    if errs:
        raise SystemExit("config errors: " + "; ".join(errs))
    provider = GeminiLiveProvider(s.gemini.api_key, s.live.model, s.live.connect_timeout_s, s.live.api_version or None)
    cfg = VoiceSessionConfig(system_instruction="You are a concise assistant. Answer in one short sentence.",
                             voice_name=s.live.voice_name, input_transcription=False, output_transcription=False)
    t0 = time.monotonic()
    session = await provider.open(cfg)
    report: dict = {"connect_ms": round((time.monotonic() - t0) * 1000, 1), "audio_bytes": 0, "sample_rate": None,
                    "first_audio_ms": None, "turn_complete": False, "events": 0}
    try:
        await session.send_text("Say hello in five words.")
        t_send = time.monotonic()

        async def consume() -> None:
            async for ev in session.events():
                report["events"] += 1
                if isinstance(ev, ResponseAudio):
                    if report["first_audio_ms"] is None:
                        report["first_audio_ms"] = round((time.monotonic() - t_send) * 1000, 1)
                    report["audio_bytes"] += len(ev.pcm)
                    report["sample_rate"] = ev.sample_rate
                elif isinstance(ev, TurnComplete):
                    report["turn_complete"] = True
                    return
                elif isinstance(ev, SessionReady):
                    continue
        await asyncio.wait_for(consume(), timeout_s)
    finally:
        await session.close()
    return report


def main() -> None:
    rep = asyncio.run(run_smoke())
    print(json.dumps(rep, indent=2))
    sys.exit(0 if rep["audio_bytes"] > 0 and rep["turn_complete"] else 1)


if __name__ == "__main__":
    main()
