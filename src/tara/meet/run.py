"""Command line for the Google Meet bot.

    python -m tara.meet.run check                      what is set up and what is missing (no browser opened)
    python -m tara.meet.run probe <link>               open the meeting page with the bot profile and report what is on screen; joins nothing
    python -m tara.meet.run login                      open Chrome with the bot's profile so YOU sign the bot's Google account in
    python -m tara.meet.run join <link>                join as Tara for a normal meeting (she answers when you speak)
    python -m tara.meet.run join <link> --interview <code>
                                                       join as Tara, wait to be admitted, greet the candidate and interview

An interview needs `TARA_WORK_ENABLED=1` and a code from `./dev.sh interview new`; a normal meeting needs neither. Tara waits in the meeting and starts
speaking when the candidate joins; with more than one other person present she stays quiet and says so on screen, because speakers
cannot be told apart in a mixed Meet stream. Nothing is recorded: audio is passed through and discarded.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..config import MeetingConfig
from ..errors import ConfigError, JoinError, TaraError
from ..settings import RuntimeSettings
from ..settings import validate as validate_settings
from . import selectors as sel
from .browser import MAC_CHROME, BrowserConfig, playwright_launcher
from .controller import DuplicateWorker, MeetController
from .worker import WorkerConfig


def chrome_path() -> str | None:
    for p in (os.environ.get("TARA_CHROME_PATH", ""), MAC_CHROME, shutil.which("google-chrome") or "", shutil.which("chromium") or ""):
        if p and Path(p).exists():
            return p
    return None


def check(settings: RuntimeSettings) -> int:
    m = settings.meet
    rows: list[tuple[str, bool, str]] = []
    try:
        import playwright  # noqa: F401
        rows.append(("Playwright", True, "installed"))
    except ImportError:
        rows.append(("Playwright", False, "run: pip install -e '.[meet]'"))
    rows.append(("Google Chrome", chrome_path() is not None, chrome_path() or "install Chrome or set TARA_CHROME_PATH"))
    prof = Path(m.profile_dir)
    rows.append(("Bot profile folder", prof.exists(), f"{prof}" + ("" if prof.exists() else "  (created by `meet login`)")))
    rows.append(("TARA_WORK_ENABLED", settings.work.enabled, "needed for interviews"))
    rows.append(("Gemini key and Live model", bool(settings.gemini.api_key and settings.live.model), "GEMINI_API_KEY and TARA_LIVE_MODEL"))
    from .devices import INSTALL_HELP, VirtualAudio, check_devices
    cables = check_devices(VirtualAudio(m.in_device, m.out_device))
    print(f"  audio route in use: {m.audio} (change with TARA_MEET_AUDIO or `join --audio`)")
    for what, ok, note in cables:
        print(f"  [{'ok' if ok else ('MISSING' if m.audio == 'devices' else 'not installed')}] {what}: {note}")
    if m.audio == "devices" and not all(ok for _, ok, _ in cables):
        print("  " + INSTALL_HELP)
        rows.append(("Virtual audio cables", False, "see above"))
    for name, ok, note in rows:
        print(f"  [{'ok' if ok else 'MISSING'}] {name}: {note}")
    print("  [manual] The bot's Google account must be signed in inside that profile (`./dev.sh meet login`) and a person must "
          "admit it from the waiting room.")
    return 0 if all(ok for _, ok, _ in rows) else 1


def login(settings: RuntimeSettings) -> int:
    exe = chrome_path()
    if exe is None:
        print("Google Chrome was not found. Install it or set TARA_CHROME_PATH.", file=sys.stderr)
        return 2
    prof = Path(settings.meet.profile_dir)
    prof.mkdir(parents=True, exist_ok=True)
    print(f"Chrome will open with the bot's own profile ({prof}).\nSign in as the BOT's Google account there (not yours), "
          f"then close the window. The password is typed into Chrome only; Tara never sees it.", flush=True)
    subprocess.run([exe, f"--user-data-dir={prof}", "--no-first-run", "--no-default-browser-check", "https://accounts.google.com"],
                   check=False)
    print("Done. Run `./dev.sh meet check` to see what is left.")
    return 0


async def probe(settings: RuntimeSettings, link: str) -> int:
    """Open the meeting page with the bot's profile and report what is on screen. Clicks nothing and joins nothing."""
    from .bridge import AudioBridge

    ctrl = MeetController(settings.meet.lock_dir)
    try:
        _, url = ctrl.parse(link)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    m = settings.meet
    launch = playwright_launcher(BrowserConfig(Path(m.profile_dir), m.channel, False), AudioBridge(lambda _b: None))
    try:
        page, close = await launch()
    except JoinError as exc:
        print(f"could not start Chrome: {exc}", file=sys.stderr)
        return 2
    try:
        await page.goto(url)
        await asyncio.sleep(8)
        raw = page.raw  # type: ignore[attr-defined]
        text = (await page.text())[:900].replace("\n", " | ")
        buttons = await raw.evaluate("""() => [...document.querySelectorAll('button,[role=button]')].filter(e => e.offsetParent !== null)
            .map(e => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\\s+/g, ' ')).filter(Boolean).slice(0, 40)""")
        inputs = await raw.evaluate("""() => [...document.querySelectorAll('input')].map(e => (e.getAttribute('aria-label') || e.placeholder || e.type))""")
        cookies = await raw.context.cookies("https://accounts.google.com")
        signed = sorted({c["name"] for c in cookies} & {"SID", "SAPISID", "__Secure-1PSID", "LSID"})
        shot = Path(m.profile_dir).parent / "probe.png"
        await raw.screenshot(path=str(shot))
        print(f"url: {raw.url}\ntitle: {await raw.title()}\nGoogle sign-in cookies in this profile: {', '.join(signed) or 'NONE (not signed in)'}")
        print(f"visible buttons: {buttons}\ninputs: {inputs}\npage text: {text}\nscreenshot: {shot}")
        page_text = await page.text()
        for label, ok in (("join button", any(any(j in b.lower() for j in sel.JOIN_BUTTONS) for b in buttons)),
                          ("sign-in text", any(t in page_text for t in sel.SIGN_IN_TEXT))):
            print(f"  {label}: {'yes' if ok else 'no'}")
    finally:
        await close()
    return 0


def _meet_meeting(reply_to_all: bool) -> MeetingConfig:
    """A normal meeting: the person in the room is the organizer. By default Tara answers any speech (right for a call with one
    person); with several people use address-only so she answers only when called by name."""
    from ..config import Mode, ParticipantSpec, Role, SpeakingPolicy
    return MeetingConfig(
        purpose="Google Meet conversation with Tara", mode=Mode.NATURAL, duration_min=60,
        policy=SpeakingPolicy.PROACTIVE_ALLOWED if reply_to_all else SpeakingPolicy.ON_ADDRESS_ONLY,
        participants=(ParticipantSpec("you", "You", Role.ORGANIZER),),
        disclosure_text="Tara is an AI participant. Audio is sent to Gemini Live. Nothing is recorded as audio or video.",
        behaviour_profile="professional")


async def join(settings: RuntimeSettings, link: str, code: str = "", headless: bool | None = None, *, section: str = "default",
               address_only: bool = False, transcripts: bool = False, audio: str = "", half_duplex: bool | None = None) -> int:
    """Join as Tara. With an interview code she runs that interview; without one it is a normal meeting."""
    from ..config import AppPolicy, Role
    from ..config import validate as validate_config
    from ..events import EventBus
    from ..memory import meeting_slug
    from ..voice.app import LiveTaraApp
    from ..voice.demo import _interview_meeting, _print_interview_result, _status_events
    from ..voice.memory import Scope

    interview = code.strip().lower()
    errors = [f"{i.field}: {i.message}" for i in validate_settings(settings) if i.severity == "error"]
    if interview and (not settings.work.enabled or not settings.work.hiring):
        errors.append("TARA_WORK_ENABLED: interviews need TARA_WORK_ENABLED=1")
    if errors:
        print("config errors: " + "; ".join(errors), file=sys.stderr)
        return 2
    ctrl = MeetController(settings.meet.lock_dir)
    cfg: MeetingConfig
    roles: dict[str, Role]
    names: dict[str, str]
    try:
        meet_code, url = ctrl.parse(link)
        if interview:
            from ..hiring.profile import CandidateProfile
            from ..hiring.workspace import Workspace, check_id
            data = Workspace(settings.work.dir).load_candidate(check_id(interview))
            prof = CandidateProfile.from_dict(data["profile"])
            cfg = _interview_meeting(prof.label, prof.role_title or "this role")
            roles, names = {"you": Role.CANDIDATE, "owner": Role.ORGANIZER}, {"you": prof.label, "owner": "Hiring team"}
            scope = Scope("local", meeting_slug(f"interview-{prof.candidate_id}"))
            who = prof.label
        else:
            cfg = _meet_meeting(reply_to_all=not address_only)
            roles, names = {"you": Role.ORGANIZER}, {"you": "You"}
            scope = Scope("local", meeting_slug(section))
            who = ""
    except Exception as exc:  # noqa: BLE001 - ConfigError, a missing interview or a bad id: all are "fix the input"
        extra = "\nCreate the interview first: ./dev.sh interview new \"Name\"  (see them: ./dev.sh interview list)" if interview else ""
        print(f"{exc}{extra}", file=sys.stderr)
        return 2
    if validate_config(cfg, AppPolicy()).errors():
        print("meeting configuration is invalid", file=sys.stderr)
        return 2
    m = settings.meet
    route = (audio or m.audio).lower()
    if route not in ("page", "devices"):
        print("--audio must be page or devices", file=sys.stderr)
        return 2
    devices_audio = None
    cables = None
    if route == "devices":
        from ..adapters.audio_device import AudioDeviceAdapter, SoundDeviceBackend
        from .devices import INSTALL_HELP, VirtualAudio, check_devices
        problems = [what for what, ok, _ in check_devices(VirtualAudio(m.in_device, m.out_device)) if not ok]
        if problems:
            print("The virtual audio cables are not available: " + "; ".join(problems) + f".\n{INSTALL_HELP}", file=sys.stderr)
            return 2
        try:
            devices_audio = AudioDeviceAdapter(SoundDeviceBackend(input_device=m.in_device, output_device=m.out_device), stream_id="you",
                                               out_channels=2)
        except JoinError as exc:
            print(f"audio device problem: {exc}", file=sys.stderr)
            return 2
        cables = {"mic": m.out_device, "speaker": m.in_device}       # Chrome's microphone is the voice cable, its speaker the meeting cable
    browser = BrowserConfig(Path(m.profile_dir), m.channel, m.headless if headless is None else headless, audio_mode=route)
    session_id = f"meet-{int(time.time())}"
    holder: dict[str, LiveTaraApp] = {}

    async def greet() -> None:
        app = holder.get("app")
        if app is not None and interview:
            await app.say_pending_opening()

    from .adapter import MeetAdapter
    log_path = Path(m.profile_dir).parent / "last-run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} meet run: meeting {meet_code}, interview={interview or 'none'}, "
                   f"mode={'address-only' if address_only else 'reply-to-all'}, audio={route}, "
                   f"half_duplex={m.half_duplex if half_duplex is None else half_duplex}\n")

    def log_line(msg: str) -> None:
        log_file.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        log_file.flush()

    def say(msg: str) -> None:
        print(f"[meet] {msg}", flush=True)
        log_line(f"[meet] {msg}")

    adapter = MeetAdapter(
        ctrl, lambda bridge: playwright_launcher(browser, bridge), session_id=session_id, interview_code=interview, url=url,
        candidate_stream_id="you",
        worker_cfg=WorkerConfig(bot_name=m.bot_name, join_timeout_s=m.join_timeout_s, admit_timeout_s=m.admit_timeout_s,
                                max_reconnects=m.reconnects),
        on_status=say, on_first_participant=greet, log=log_line, audio=devices_audio, cables=cables,
        half_duplex=m.half_duplex if half_duplex is None else half_duplex)
    bus = EventBus()
    app = LiveTaraApp(settings, cfg, roles, names, adapter, session_id=session_id, scope=scope, bus=bus, user_id="you",
                      interview_code=interview, defer_opening=bool(interview))
    holder["app"] = app
    if transcripts:
        def show(kind: str, text: str) -> None:
            print(f"[{kind}] {text}", flush=True)
            log_line(f"[{kind}] {text}")

        app.engine.text_sink = show
    status = asyncio.create_task(_status_events(bus))

    async def engine_events() -> None:
        """Every decision the engine makes about speaking (allowed, denied and why, interrupted) goes to the run log, no content."""
        sub = bus.subscribe("live.")
        keys = ("response", "state", "reason", "origin", "delivered_ms", "tool", "ok", "error", "source")
        while True:
            ev = await sub.get()
            log_line(f"[engine] {ev.name} " + " ".join(f"{k}={ev.data[k]}" for k in keys if k in ev.data))
            if ev.name == "live.gate" and ev.data.get("state") == "denied":
                why = str(ev.data.get("reason", ""))
                meaning = {"not_addressed": "you did not call her by name: say \"Tara, ...\" or run without --address-only",
                           "no_transcript": "her words could not be matched to anything you said",
                           "floor_timeout": "someone kept talking, so she did not talk over them",
                           "muted": "she is muted", "takeover": "someone took over from her"}.get(why, why)
                say(f"Tara has a reply but is staying quiet: {meaning}.")

    engine_log = asyncio.create_task(engine_events())
    try:
        print(f"Joining meeting {meet_code} as {m.bot_name}. If the meeting needs approval, admit the bot from the waiting room.", flush=True)
        await app.start()
        if interview:
            print(f"Tara is in the meeting. She will greet {who} when they join. Ctrl+C ends the interview.", flush=True)
        elif address_only:
            print("Tara is in the meeting. Say \"Tara, ...\" to talk to her. Ctrl+C ends the session.", flush=True)
        else:
            print("Tara is in the meeting and will answer when you speak (use --address-only to make her wait to be called by name). "
                  "Ctrl+C ends the session.", flush=True)
        await app.engine.wait_closed()
        print("The meeting session ended.", flush=True)
        log_line("session ended")
    except DuplicateWorker as exc:
        print(f"not started: {exc}", file=sys.stderr)
        return 3
    except (JoinError, TaraError) as exc:
        print(f"could not start: {exc}", file=sys.stderr)
        return 2
    finally:
        status.cancel()
        engine_log.cancel()
        await asyncio.gather(status, engine_log, return_exceptions=True)
        await app.close()
        _print_interview_result(app)
        log_file.close()
        print(f"Run log saved to {log_path}", flush=True)
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tara.meet.run", description="Google Meet bot for interviews.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="show what is set up")
    sub.add_parser("login", help="sign the bot's Google account in (opens Chrome)")
    pr = sub.add_parser("probe", help="open a meeting page with the bot profile and report what is on screen (joins nothing)")
    pr.add_argument("link")
    j = sub.add_parser("join", help="join a meeting as Tara (a normal meeting, or an interview with --interview)")
    j.add_argument("link", help="https://meet.google.com/abc-defg-hij or abc-defg-hij")
    j.add_argument("--interview", default="", metavar="CODE", help="run an interview made with `./dev.sh interview new`; leave out for a normal meeting")
    j.add_argument("--section", default=os.environ.get("TARA_SECTION", "default"), help="memory section for a normal meeting (default: TARA_SECTION or default)")
    j.add_argument("--type", dest="meeting_type", default=os.environ.get("TARA_MEETING_TYPE", "general"),
                   help="meeting type: general, standup, one_on_one, retro, client_call, brainstorm")
    j.add_argument("--address-only", action="store_true", help="Tara answers only when called by name (use this with several people)")
    j.add_argument("--transcripts", action="store_true", help="print what Tara heard and said on this screen (shows speech)")
    j.add_argument("--audio", choices=("page", "devices"), default="", help="audio route: page (taps in the page) or devices (two virtual "
                   "audio cables, see docs/GOOGLE_MEET_BOT.md); default TARA_MEET_AUDIO")
    j.add_argument("--half-duplex", action="store_true", default=None, help="silence the meeting audio while Tara speaks, so she cannot hear "
                   "her own voice come back from someone's speaker (use this when a phone or laptop speaker is being used); you cannot "
                   "interrupt her by voice")
    j.add_argument("--headless", action="store_true", default=None, help="no visible window (not recommended for first tests)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = {**os.environ, "TARA_LIVE_ENABLED": "1"}
    if args.cmd == "join":
        env["TARA_MEETING_TYPE"] = args.meeting_type
    settings = RuntimeSettings.from_env(env)
    try:
        if args.cmd == "check":
            return check(settings)
        if args.cmd == "login":
            return login(settings)
        if args.cmd == "probe":
            return asyncio.run(probe(settings, args.link))
        return asyncio.run(join(settings, args.link, args.interview, args.headless, section=args.section,
                                address_only=args.address_only, transcripts=args.transcripts, audio=args.audio, half_duplex=args.half_duplex))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
