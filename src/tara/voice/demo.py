"""Minimal local voice demo: microphone in, Tara's voice out.

Run (headphones required to avoid feedback):
    set -a; source .env; set +a
    .venv/bin/python -m tara.voice.demo

Address Tara by name, for example "Tara, what do you think?". The normal participation
gate still decides whether she may speak; this demo does not bypass it. Ctrl+C stops.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

from ..adapters.audio_device import AudioDeviceAdapter
from ..config import AppPolicy, MeetingConfig, Mode, ParticipantSpec, Role, SpeakingPolicy
from ..config import validate as validate_config
from ..errors import JoinError, TaraError
from ..events import EventBus
from ..settings import RuntimeSettings
from ..settings import validate as validate_settings
from .app import LiveTaraApp
from .memory import Scope

_DEBUG_KEYS = ("live_input_transcripts", "live_input_transcript_finals", "live_response_audio_chunks",
               "live_output_transcripts", "live_turn_complete", "gate_allowed", "gate_denied",
               "live_send_errors", "live_downlink_errors", "live_disconnects", "live_reconnects",
               "audio_invalid", "live_uplink_dropped", "playback_errors")
_DEBUG_FIELDS = ("response", "state", "reason", "origin", "source", "delivered_ms", "length", "connect_ms",
                 "time_left_s", "recoverable")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tara.voice.demo", description="Local Tara voice conversation.")
    p.add_argument("--profile", choices=("professional", "conversational", "expressive"), default="professional",
                   help="style profile (default: professional, the only profile observed in a real live run)")
    p.add_argument("--debug", action="store_true",
                   help="print non-content session, gate and playback counters (never speech or transcripts)")
    p.add_argument("--one-on-one", action="store_true",
                   help="reply to any speech from the single participant; the default requires 'Tara, ...'")
    p.add_argument("--speaker", action="store_true",
                   help="speaker output: mute the mic while Tara speaks to stop echo feedback (disables voice barge-in)")
    return p


def _meeting(profile: str, one_on_one: bool = False) -> MeetingConfig:
    return MeetingConfig(
        purpose="Local voice conversation with Tara",
        mode=Mode.NATURAL,
        duration_min=30,
        policy=SpeakingPolicy.PROACTIVE_ALLOWED if one_on_one else SpeakingPolicy.ON_ADDRESS_ONLY,
        participants=(ParticipantSpec("you", "You", Role.ORGANIZER),),
        disclosure_text="This is a local Tara voice demo. Audio is sent to Gemini Live.",
        behaviour_profile=profile,
    )


async def _debug_events(bus: EventBus, app: LiveTaraApp) -> None:
    """Print only counters and event metadata; never participant text or audio."""
    sub = bus.subscribe("live.")
    last = 0.0
    while True:
        try:
            ev = await asyncio.wait_for(sub.get(), timeout=2.0)
        except TimeoutError:
            ev = None
        if ev is not None:
            data = {k: ev.data[k] for k in _DEBUG_FIELDS if k in ev.data}
            print(f"[debug] {ev.name} {data}", flush=True)
        now = time.monotonic()
        if now - last >= 5.0:
            last = now
            c = app.metrics.counters
            parts = [f"{k}={c[k]}" for k in _DEBUG_KEYS if c[k]]
            parts.append(f"uplink_depth={app.metrics.gauges.get('live_uplink_depth', 0):.0f}")
            print("[debug] counters " + " ".join(parts), flush=True)


def _print_summary(app: LiveTaraApp, adapter: AudioDeviceAdapter) -> None:
    c = app.metrics.counters
    parts = [f"{k}={c[k]}" for k in _DEBUG_KEYS if c[k]]
    print("session summary: " + (" ".join(parts) if parts else "no live events counted"), flush=True)
    print(f"adapter: input_chunks={adapter.input_chunks} input_bytes={adapter.input_bytes} "
          f"dropped={adapter.dropped_chunks} playback_failures={adapter.playback_failures} "
          f"half_duplex={adapter.half_duplex}", flush=True)


async def run(profile: str, debug: bool = False, one_on_one: bool = False, speaker: bool = False) -> int:
    settings = RuntimeSettings.from_env({**os.environ, "TARA_LIVE_ENABLED": "1"})
    errors = [f"{i.field}: {i.message}" for i in validate_settings(settings) if i.severity == "error"]
    cfg = _meeting(profile, one_on_one)
    errors += [f"{i.field}: {i.message}" for i in validate_config(cfg, AppPolicy()).errors()]
    if errors:
        print("config errors: " + "; ".join(errors), file=sys.stderr)
        return 2

    bus = EventBus() if debug else None
    adapter = AudioDeviceAdapter(stream_id="you", half_duplex=speaker)
    app = LiveTaraApp(
        settings, cfg, {"you": Role.ORGANIZER}, {"you": "You"}, adapter,
        session_id="local-voice-demo", scope=Scope("local", "voice-demo"), bus=bus,
    )
    debug_task: asyncio.Task | None = None
    try:
        await app.start()
        if debug and bus is not None:
            debug_task = asyncio.create_task(_debug_events(bus, app))
        if speaker:
            print("Listening. Speaker mode: the mic is muted while Tara speaks. Ctrl+C stops.")
        elif one_on_one:
            print("Listening. Speak normally and wait for the reply. Headphones are required. Ctrl+C stops.")
        else:
            print("Listening. Say 'Tara, ...' and wait for the reply. Headphones are required. Ctrl+C stops.")
        await app.engine.wait_closed()
        print("Session ended.", flush=True)
    except JoinError as exc:
        print(f"audio error: {exc}", file=sys.stderr)
        return 2
    except TaraError as exc:
        print(f"startup error: {exc}", file=sys.stderr)
        return 2
    finally:
        if debug_task is not None:
            debug_task.cancel()
            await asyncio.gather(debug_task, return_exceptions=True)
        try:
            await app.close()
        finally:
            _print_summary(app, adapter)
    return 0


def main() -> int:
    args = _parser().parse_args()
    try:
        return asyncio.run(run(args.profile, args.debug, args.one_on_one, args.speaker))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
