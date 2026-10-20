"""Command line for the Google Meet bot.

    python -m tara.meet.run check                      what is set up and what is missing (no browser opened)
    python -m tara.meet.run login                      open Chrome with the bot's profile so YOU sign the bot's Google account in
    python -m tara.meet.run join <link> --interview <code>
                                                       join the meeting as Tara, wait to be admitted, greet the candidate and interview

`join` needs `TARA_WORK_ENABLED=1` and an interview created with `./dev.sh interview new`. Tara waits in the meeting and starts
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

from ..errors import ConfigError, JoinError, TaraError
from ..settings import RuntimeSettings
from ..settings import validate as validate_settings
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


async def join(settings: RuntimeSettings, link: str, code: str, headless: bool | None) -> int:
    from ..config import AppPolicy, Role
    from ..config import validate as validate_config
    from ..events import EventBus
    from ..hiring.profile import CandidateProfile
    from ..hiring.workspace import Workspace, WorkspaceError, check_id
    from ..memory import meeting_slug
    from ..voice.app import LiveTaraApp
    from ..voice.demo import _interview_meeting, _print_interview_result, _status_events
    from ..voice.memory import Scope

    errors = [f"{i.field}: {i.message}" for i in validate_settings(settings) if i.severity == "error"]
    if not settings.work.enabled or not settings.work.hiring:
        errors.append("TARA_WORK_ENABLED: interviews need TARA_WORK_ENABLED=1")
    if errors:
        print("config errors: " + "; ".join(errors), file=sys.stderr)
        return 2
    ctrl = MeetController(settings.meet.lock_dir)
    try:
        meet_code, url = ctrl.parse(link)
        data = Workspace(settings.work.dir).load_candidate(check_id(code.strip().lower()))
    except (ConfigError, WorkspaceError, ValueError) as exc:
        print(f"{exc}\nCreate the interview first: ./dev.sh interview new \"Name\"  (see them: ./dev.sh interview list)", file=sys.stderr)
        return 2
    prof = CandidateProfile.from_dict(data["profile"])
    cfg = _interview_meeting(prof.label, prof.role_title or "this role")
    if validate_config(cfg, AppPolicy()).errors():
        print("interview configuration is invalid", file=sys.stderr)
        return 2
    m = settings.meet
    browser = BrowserConfig(Path(m.profile_dir), m.channel, m.headless if headless is None else headless)
    session_id = f"meet-{int(time.time())}"
    holder: dict[str, LiveTaraApp] = {}

    async def greet() -> None:
        app = holder.get("app")
        if app is not None:
            await app.say_pending_opening()

    from .adapter import MeetAdapter
    adapter = MeetAdapter(
        ctrl, lambda bridge: playwright_launcher(browser, bridge), session_id=session_id, interview_code=code.strip().lower(), url=url,
        candidate_stream_id="you",
        worker_cfg=WorkerConfig(bot_name=m.bot_name, join_timeout_s=m.join_timeout_s, admit_timeout_s=m.admit_timeout_s,
                                max_reconnects=m.reconnects),
        on_status=lambda msg: print(f"[meet] {msg}", flush=True), on_first_participant=greet)
    bus = EventBus()
    app = LiveTaraApp(settings, cfg, {"you": Role.CANDIDATE, "owner": Role.ORGANIZER}, {"you": prof.label, "owner": "Hiring team"},
                      adapter, session_id=session_id, scope=Scope("local", meeting_slug(f"interview-{prof.candidate_id}")), bus=bus,
                      user_id="you", interview_code=code.strip().lower(), defer_opening=True)
    holder["app"] = app
    status = asyncio.create_task(_status_events(bus))
    try:
        print(f"Joining meeting {meet_code} as {m.bot_name}. If the meeting needs approval, admit the bot from the waiting room.", flush=True)
        await app.start()
        print(f"Tara is in the meeting. She will greet {prof.label} when they join. Ctrl+C ends the interview.", flush=True)
        await app.engine.wait_closed()
        print("The meeting session ended.", flush=True)
    except DuplicateWorker as exc:
        print(f"not started: {exc}", file=sys.stderr)
        return 3
    except (JoinError, TaraError) as exc:
        print(f"could not start: {exc}", file=sys.stderr)
        return 2
    finally:
        status.cancel()
        await asyncio.gather(status, return_exceptions=True)
        await app.close()
        _print_interview_result(app)
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tara.meet.run", description="Google Meet bot for interviews.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="show what is set up")
    sub.add_parser("login", help="sign the bot's Google account in (opens Chrome)")
    j = sub.add_parser("join", help="join a meeting and run an interview")
    j.add_argument("link", help="https://meet.google.com/abc-defg-hij or abc-defg-hij")
    j.add_argument("--interview", required=True, metavar="CODE", help="a code from `./dev.sh interview new`")
    j.add_argument("--headless", action="store_true", default=None, help="no visible window (not recommended for first tests)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = RuntimeSettings.from_env({**os.environ, "TARA_LIVE_ENABLED": "1"})
    try:
        if args.cmd == "check":
            return check(settings)
        if args.cmd == "login":
            return login(settings)
        return asyncio.run(join(settings, args.link, args.interview, args.headless))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
