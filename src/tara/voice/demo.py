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
from ..adapters.output_detect import detect_output
from ..core.config import AppPolicy, MeetingConfig, Mode, ParticipantSpec, Role, SpeakingPolicy
from ..core.config import validate as validate_config
from ..core.errors import JoinError, TaraError
from ..core.events import EventBus
from ..core.settings import RuntimeSettings
from ..core.settings import validate as validate_settings
from ..memory import meeting_slug
from .app import LiveTaraApp
from .memory import Scope

_DEBUG_KEYS = ("live_input_transcripts", "live_input_transcript_finals", "live_response_audio_chunks",
               "live_output_transcripts", "live_turn_complete", "gate_allowed", "gate_denied",
               "live_send_errors", "live_downlink_errors", "live_disconnects", "live_reconnects",
               "audio_invalid", "live_uplink_dropped", "playback_errors", "memory_writes", "memory_recalls",
               "memory_write_failures", "memory_recall_failures", "memory_startup_failures",
               "interview_question_clocks_started", "interview_question_playback_incomplete",
               "interview_question_timeouts", "interview_eval_fallbacks", "interview_eval_model")
_DEBUG_FIELDS = ("response", "state", "reason", "origin", "source", "delivered_ms", "length", "connect_ms",
                 "time_left_s", "recoverable", "tool", "ok", "error")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tara.voice.demo", description="Local Tara voice conversation.")
    p.add_argument("--profile", choices=("professional", "conversational", "expressive"), default="professional",
                   help="style profile (default: professional, the only profile observed in a real live run)")
    p.add_argument("--debug", action="store_true",
                   help="print non-content session, gate and playback counters (never speech or transcripts)")
    p.add_argument("--one-on-one", action="store_true",
                   help="reply to any speech from the single participant; the default requires 'Tara, ...'")
    p.add_argument("--output", choices=("auto", "headphones", "speaker"), default="auto",
                   help="where Tara's voice goes. auto (default) guesses from the OS and falls back to 'speaker' when unsure; "
                        "'speaker' mutes the mic while Tara speaks to stop echo (no voice barge-in); 'headphones' allows barge-in")
    p.add_argument("--speaker", dest="output", action="store_const", const="speaker", help="same as --output speaker")
    p.add_argument("--headphones", dest="output", action="store_const", const="headphones", help="same as --output headphones")
    p.add_argument("--section", "--meeting", dest="meeting",
                   default=os.environ.get("TARA_SECTION", os.environ.get("TARA_MEETING_NAME", "default")),
                   help="section (a chat) name. Every run of the same section is its next numbered meeting and remembers the "
                        "earlier ones; a new name starts a separate section (default: TARA_SECTION or 'default')")
    p.add_argument("--type", dest="meeting_type", default=os.environ.get("TARA_MEETING_TYPE", "general"),
                   help="meeting type: general, standup, one_on_one, retro, client_call or brainstorm (default: general)")
    p.add_argument("--transcripts", action="store_true",
                   help="print what Tara heard and said on this screen (local only; off by default because it shows speech)")
    p.add_argument("--interview", default="", metavar="CODE",
                   help="run an interview prepared with `./dev.sh interview new`: Tara greets the candidate, confirms their name "
                        "and runs the questions (you play the candidate when testing alone)")
    p.add_argument("--list-meetings", action="store_true", help="list sections and their numbered meetings with dates, then exit")
    return p


def _interview_meeting(label: str, role: str) -> MeetingConfig:
    """A Tara-led interview with one candidate (the person at this microphone) and the hiring team listening in."""
    return MeetingConfig(
        purpose=f"Interview for {role}", mode=Mode.INTERVIEW_STRUCTURED, duration_min=45, policy=SpeakingPolicy.ON_ADDRESS_ONLY,
        participants=(ParticipantSpec("you", label, Role.CANDIDATE), ParticipantSpec("owner", "Hiring team", Role.ORGANIZER)),
        disclosure_text="This interview is run by Tara, an AI interviewer. Audio is sent to Gemini Live and the interview is "
                        "recorded for the hiring team.",
        behaviour_profile="professional", lead_interview=True,
    )


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


async def _status_events(bus: EventBus) -> None:
    """Always on: print problems in plain words, because a voice that fails silently is the worst case."""
    sub = bus.subscribe("live.")
    notes = {"live.failed": "The session failed and is closing", "live.reconnecting": "Connection lost, reconnecting...",
             "live.reconnected": "Reconnected.", "live.disconnected": "The connection dropped",
             "live.goaway": "The service is asking us to reconnect soon"}
    while True:
        ev = await sub.get()
        if ev.name in notes:
            reason = ev.data.get("reason") or ev.data.get("recoverable") or ""
            print(f"[problem] {notes[ev.name]}" + (f" ({reason})" if reason != "" else ""), flush=True)
        elif ev.name == "live.tool" and not ev.data.get("ok", True):
            why = ev.data.get("reason") or ""
            print(f"[problem] {ev.data.get('tool')} failed: {ev.data.get('error')}" + (f" - {why}" if why else ""), flush=True)


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


def _print_interview_result(app: LiveTaraApp) -> None:
    f = app.last_finalize
    if not f.get("interview_id"):
        return
    print("\nInterview result (decision support, a person must review it):", flush=True)
    for name, avg in f.get("criteria", []):
        print(f"  {name}: {avg if avg is not None else 'insufficient evidence'}", flush=True)
    for flag in f.get("flags", []):
        print(f"  flag: {flag}", flush=True)
    if f.get("report_path"):
        print(f"  report saved: {f['report_path']}", flush=True)


def _print_summary(app: LiveTaraApp, adapter: AudioDeviceAdapter) -> None:
    c = app.metrics.counters
    parts = [f"{k}={c[k]}" for k in _DEBUG_KEYS if c[k]]
    print("session summary: " + (" ".join(parts) if parts else "no live events counted"), flush=True)
    print(f"adapter: input_chunks={adapter.input_chunks} input_bytes={adapter.input_bytes} "
          f"dropped={adapter.dropped_chunks} playback_failures={adapter.playback_failures} "
          f"half_duplex={adapter.half_duplex}", flush=True)
    print(f"memory: {app.health().get('memory', 'disabled')}", flush=True)


async def list_meetings() -> int:
    from ..memory import MemoryScope, PostgresMemoryStore
    settings = RuntimeSettings.from_env(dict(os.environ))
    if not settings.memory.enabled:
        print("Memory is off (set TARA_MEMORY_ENABLED=1).")
        return 0
    store = PostgresMemoryStore(settings.memory.database_url, schema=settings.memory.schema or None)
    try:
        rows = await store.list_meetings(MemoryScope("local", "you"))
        if not rows:
            print("No sections recorded yet.")
        for r in rows:
            sc = MemoryScope("local", "you", meeting_id=str(r["meeting_id"]))
            print(f"section {r['meeting_id']}  ({r['items']} records)")
            for m in await store.list_sessions(sc):
                end = m.ended_at.astimezone().strftime("%H:%M") if m.ended_at else "end not recorded"
                print(f"   meeting {m.number}: {m.started_at.astimezone().strftime('%a %d %b %Y %H:%M')} to {end}")
    finally:
        await store.close()
    return 0


async def run(profile: str, debug: bool = False, one_on_one: bool = False, speaker: bool = False,
              output: str = "", meeting: str = "default", transcripts: bool = False,
              meeting_type: str = "general", interview: str = "") -> int:
    settings = RuntimeSettings.from_env({**os.environ, "TARA_LIVE_ENABLED": "1", "TARA_MEETING_TYPE": meeting_type})
    errors = [f"{i.field}: {i.message}" for i in validate_settings(settings) if i.severity == "error"]
    roles: dict[str, Role] = {"you": Role.ORGANIZER}
    names: dict[str, str] = {"you": "You"}
    if interview:
        if not settings.work.enabled or not settings.work.hiring:
            print("config errors: interviews need TARA_WORK_ENABLED=1 (and TARA_HIRING_ENABLED not 0)", file=sys.stderr)
            return 2
        from ..hiring.profile import CandidateProfile
        from ..hiring.workspace import Workspace, WorkspaceError, check_id
        try:
            data = Workspace(settings.work.dir).load_candidate(check_id(interview.strip().lower()))
        except (WorkspaceError, ValueError):
            print(f"No prepared interview with code {interview!r}. Create one with: ./dev.sh interview new \"Name\"  "
                  f"(see them with ./dev.sh interview list)", file=sys.stderr)
            return 2
        prof = CandidateProfile.from_dict(data["profile"])
        cfg = _interview_meeting(prof.label, prof.role_title or "this role")
        roles, names = {"you": Role.CANDIDATE, "owner": Role.ORGANIZER}, {"you": prof.label, "owner": "Hiring team"}
        meeting = f"interview-{prof.candidate_id}"
    else:
        cfg = _meeting(profile, one_on_one)
    errors += [f"{i.field}: {i.message}" for i in validate_config(cfg, AppPolicy()).errors()]
    if errors:
        print("config errors: " + "; ".join(errors), file=sys.stderr)
        return 2

    mode = output or ("speaker" if speaker else "headphones")
    if mode == "auto":
        guess = detect_output()
        mode = guess.kind
        print(f"Output auto-detected as {guess.kind} ({'confident' if guess.confident else 'not sure, so the safe choice'}: "
              f"{guess.reason}). Override with --headphones or --speaker.", flush=True)
    speaker = mode == "speaker"
    print(f"Section: {meeting_slug(meeting)}  (each run is its next numbered meeting; a new name starts a fresh section)", flush=True)
    bus = EventBus()
    adapter = AudioDeviceAdapter(stream_id="you", half_duplex=speaker)
    app = LiveTaraApp(
        settings, cfg, roles, names, adapter,
        session_id=f"demo-{int(time.time())}", scope=Scope("local", meeting_slug(meeting)), bus=bus, user_id="you",
        interview_code=interview.strip().lower(),
    )
    debug_task: asyncio.Task | None = None
    if transcripts:
        app.engine.text_sink = lambda kind, text: print(f"[{kind}] {text}", flush=True)
    try:
        await app.start()
        if interview:
            print(f"Interview mode: you are the candidate, {names['you']}. Tara will greet you and ask for your name first.", flush=True)
        if app.meeting is not None:
            print(f"This is meeting {app.meeting.number} of section '{meeting_slug(meeting)}' ({meeting_type}).", flush=True)
        elif app.memory_service is None:
            print("Memory is off, so nothing from this run will be kept.", flush=True)
        print(f"Work features (recorder, secretary, documents): {'on' if app.work is not None else 'OFF (TARA_WORK_ENABLED=1 to enable)'}",
              flush=True)
        debug_task = asyncio.create_task(_debug_events(bus, app) if debug else _status_events(bus))
        if speaker:
            print("Listening. Speaker mode: the mic is muted while Tara speaks. Ctrl+C stops.")
        elif one_on_one:
            print("Listening. Speak normally and wait for the reply. Use headphones for voice interruption. Ctrl+C stops.")
        else:
            print("Listening. Say 'Tara, ...' and wait for the reply. Use headphones for voice interruption. Ctrl+C stops.")
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
            _print_interview_result(app)
    return 0


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.list_meetings:
            return asyncio.run(list_meetings())
        return asyncio.run(run(args.profile, args.debug, args.one_on_one, output=args.output, meeting=args.meeting,
                              transcripts=args.transcripts, meeting_type=args.meeting_type, interview=args.interview))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
