"""Command line for the Google Meet bot.

    python -m tara.meet.run check                      what is set up and what is missing (no browser opened)
    python -m tara.meet.run probe <link>               open the meeting page with the bot profile and report what is on screen; joins nothing
    python -m tara.meet.run login                      open Chrome with the bot's profile so YOU sign the bot's Google account in
    python -m tara.meet.run join <link>                join as Tara for a normal meeting (she answers when you speak)
    python -m tara.meet.run join <link> --per-participant
                                                       hear each participant on their own channel and name it from Meet's speaking glow
                                                       (page audio route only; see docs/google-meet/SPEAKER_BINDING.md)
    python -m tara.meet.run join <link> --interview <code>
                                                       join as Tara, wait to be admitted, greet the candidate and interview

An interview needs `TARA_WORK_ENABLED=1` and a code from `./dev.sh interview new`; a normal meeting needs neither. Tara waits in the meeting and starts
speaking when the candidate joins; with more than one other person present she stays quiet and says so on screen, because speakers
cannot be told apart in a mixed Meet stream. Nothing is recorded unless `--record` is given: audio is passed through and discarded.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..core.config import MeetingConfig
from ..core.errors import ConfigError, JoinError, TaraError
from ..core.settings import RuntimeSettings
from ..core.settings import validate as validate_settings
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


def speaking_policy(people: int, mode: str):  # noqa: ANN201 - returns config.SpeakingPolicy
    """Who Tara answers. `auto`: any speech while one other person is here, only her name once there are two or more (several people
    talk to each other, and answering all of it would be rude and confusing). `all` and `address` are fixed."""
    from ..core.config import SpeakingPolicy
    if mode == "all":
        return SpeakingPolicy.PROACTIVE_ALLOWED
    if mode == "address":
        return SpeakingPolicy.ON_ADDRESS_ONLY
    return SpeakingPolicy.ON_ADDRESS_ONLY if people >= 2 else SpeakingPolicy.PROACTIVE_ALLOWED


def _disclosure(text: str, record: bool) -> str:
    """The AI-presence line the meeting is told. With `--record` it must never still say that nothing is recorded."""
    if not record:
        return text
    note = "The meeting audio and Tara's voice are being recorded on this computer."
    return text.replace("Nothing is recorded as audio or video.", note).strip() or note


def _meet_meeting(reply_to_all: bool, wake_by_model: bool = False) -> MeetingConfig:
    """A normal meeting: the person in the room is the organizer. By default Tara answers any speech (right for a call with one
    person); with several people use address-only so she answers only when called by name."""
    from ..core.config import Mode, ParticipantSpec, Role, SpeakingPolicy
    return MeetingConfig(
        purpose="Google Meet conversation with Tara", mode=Mode.NATURAL, duration_min=60,
        policy=SpeakingPolicy.PROACTIVE_ALLOWED if reply_to_all else SpeakingPolicy.ON_ADDRESS_ONLY,
        participants=(ParticipantSpec("you", "You", Role.ORGANIZER),),
        disclosure_text="Tara is an AI participant. Audio is sent to Gemini Live. Nothing is recorded as audio or video.",
        behaviour_profile="professional", wake_by_model=wake_by_model)


async def join(settings: RuntimeSettings, link: str, code: str = "", headless: bool | None = None, *, section: str = "default",
               address_only: bool = False, transcripts: bool = False, audio: str = "", half_duplex: bool | None = None,
               reply_to_all: bool = False, camera: bool = True, model_wake: bool = False, record: bool = False, gain: bool = True,
               follow_up_s: float = 8.0, per_participant: bool = False) -> int:
    """Join as Tara. With an interview code she runs that interview; without one it is a normal meeting."""
    from ..core.config import AppPolicy, Role
    from ..core.config import validate as validate_config
    from ..core.events import EventBus
    from ..memory import meeting_slug
    from ..voice.app import LiveTaraApp
    from ..voice.demo import _interview_meeting, _print_interview_result, _status_events
    from ..voice.memory import Scope

    interview = code.strip().lower()
    answer_mode = "all" if reply_to_all and not address_only else "address"      # standby by default: listens and takes notes, answers when called
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
            cfg = _meet_meeting(reply_to_all=answer_mode != "address", wake_by_model=model_wake and answer_mode == "address")
            roles, names = {"you": Role.ORGANIZER}, {"you": "You"}
            scope = Scope("local", meeting_slug(section))
            who = ""
    except Exception as exc:  # noqa: BLE001 - ConfigError, a missing interview or a bad id: all are "fix the input"
        extra = "\nCreate the interview first: ./dev.sh interview new \"Name\"  (see them: ./dev.sh interview list)" if interview else ""
        print(f"{exc}{extra}", file=sys.stderr)
        return 2
    if record:
        cfg = dataclasses.replace(cfg, disclosure_text=_disclosure(cfg.disclosure_text, record))
    if validate_config(cfg, AppPolicy()).errors():
        print("meeting configuration is invalid", file=sys.stderr)
        return 2
    m = settings.meet
    route = (audio or m.audio).lower()
    if route not in ("page", "devices"):
        print("--audio must be page or devices", file=sys.stderr)
        return 2
    if per_participant and route != "page":
        print("--per-participant taps each participant's own media element, which only exists on the page audio route; it "
              "cannot be used with --audio devices", file=sys.stderr)
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
            devices_audio = AudioDeviceAdapter(SoundDeviceBackend(input_device=m.in_device, output_device=m.out_device, output_latency="high"),
                                               stream_id="you", out_channels=2, prebuffer_ms=200)
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

    from ..floor.audio import AdaptiveGain
    from .adapter import MeetAdapter

    def people_changed(count: int) -> None:
        """Switch who she answers when the room fills up (normal meetings only; interviews have their own rules)."""
        app_ = holder.get("app")
        if app_ is None or interview or answer_mode != "auto":
            return
        part = app_.engine.participation
        wanted = speaking_policy(count, answer_mode)
        if wanted is not part.cfg.policy:
            part.cfg = dataclasses.replace(part.cfg, policy=wanted)
            say("two or more people are here: Tara now answers only when called by name (\"Tara, ...\")." if count >= 2
                else "one other person is here: Tara answers when they speak.")

    log_path = Path(m.profile_dir).parent / "last-run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} meet run: meeting {meet_code}, interview={interview or 'none'}, "
                   f"answers={answer_mode}, audio={route}, per_participant={per_participant}, "
                   f"half_duplex={m.half_duplex if half_duplex is None else half_duplex}\n")

    def log_line(msg: str) -> None:
        log_file.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        log_file.flush()

    def say(msg: str) -> None:
        print(f"[meet] {msg}", flush=True)
        log_line(f"[meet] {msg}")

    recorder = None
    if record:
        from .recorder import MeetingRecorder
        recorder = MeetingRecorder(Path(m.profile_dir).parent / "recordings", f"{meet_code}-{time.strftime('%Y%m%d-%H%M%S')}")
        say(f"RECORDING is on: audio is saved on this computer in {recorder.room.path.parent}. Tell everyone in the meeting.")
    picture = ""
    if camera:
        image = Path(__file__).resolve().parents[3] / "image" / "image.png"
        if image.is_file():
            try:
                from .bridge import make_picture
                picture = make_picture(image, 'Call me: "Hey Tara". I\'m here to help.')
            except Exception as exc:  # noqa: BLE001 - the picture is optional; the meeting must not depend on it
                say(f"no camera picture ({type(exc).__name__}); Tara joins with the camera off")
    adapter = MeetAdapter(
        ctrl, lambda bridge: playwright_launcher(browser, bridge), session_id=session_id, interview_code=interview, url=url,
        candidate_stream_id="you",
        worker_cfg=WorkerConfig(bot_name=m.bot_name, join_timeout_s=m.join_timeout_s, admit_timeout_s=m.admit_timeout_s,
                                max_reconnects=m.reconnects, camera_on=bool(picture)),
        on_status=say, on_first_participant=greet, log=log_line, audio=devices_audio, cables=cables,
        half_duplex=m.half_duplex if half_duplex is None else half_duplex, on_people=people_changed, picture=picture, recorder=recorder,
        gain=AdaptiveGain() if gain else None, per_participant=per_participant, self_name=m.bot_name)
    bus = EventBus()
    app = LiveTaraApp(settings, cfg, roles, names, adapter, session_id=session_id, scope=scope, bus=bus, user_id="you",
                      interview_code=interview, defer_opening=bool(interview))
    holder["app"] = app
    app.engine.speaker_hint = adapter.speaker_for_turn
    if app.work is not None:
        app.work.guest_source = lambda: list(adapter.seen_people)
    app.engine.participation.follow_up_s = 0.0 if interview or answer_mode == "all" else follow_up_s
    app.engine.p = dataclasses.replace(app.engine.p, adaptive_vad=True, reconnect_attempts=8)       # a call's background noise must not count as someone talking
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
        elif answer_mode == "address":
            print("Tara is in the meeting. Say \"Tara, ...\" to talk to her. Ctrl+C ends the session.", flush=True)
        elif answer_mode == "all":
            print("Tara is in the meeting and answers any speech. Ctrl+C ends the session.", flush=True)
        else:
            print("Tara is in the meeting. She answers any speech while one other person is here, and only \"Tara, ...\" once there are two or "
                  "more (--address-only or --reply-to-all fix it either way). Ctrl+C ends the session.", flush=True)
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
        _print_minutes(app)
        log_file.close()
        print(f"Run log saved to {log_path}", flush=True)
    return 0


def _print_minutes(app: Any) -> None:
    f = app.last_finalize
    if f.get("recorded"):
        print(f"Notes kept: {f['recorded']}.", flush=True)
    if f.get("emailed"):
        print(f"Minutes email: {f['emailed']}.", flush=True)
    if f.get("minutes_file"):
        print(f"Minutes saved: {f['minutes_file']} (in the work documents folder).", flush=True)


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
    j.add_argument("--address-only", action="store_true", help="Tara answers only when called by name, always")
    j.add_argument("--reply-to-all", action="store_true", help="Tara answers any speech (default: she listens and takes notes, and answers only when called, like \"Hey Tara\")")
    j.add_argument("--model-wake", action="store_true", help="standby where the model itself decides whether it was called by name (use when the transcript misses her name); experimental")
    j.add_argument("--record", action="store_true", help="save the meeting audio (room and Tara) as WAV files under work/meet/recordings; tell everyone first")
    j.add_argument("--follow-up", type=float, default=8.0, metavar="SECONDS",
                   help="after she answers, keep talking to her this long without saying her name again (0 = off, default 8)")
    j.add_argument("--no-gain", action="store_true", help="do not lift quiet voices (automatic volume for what Tara hears)")
    j.add_argument("--no-camera", action="store_true", help="join with the camera off instead of showing Tara's picture")
    j.add_argument("--transcripts", action="store_true", help="print what Tara heard and said on this screen (shows speech)")
    j.add_argument("--audio", choices=("page", "devices"), default="", help="audio route: page (taps in the page) or devices (two virtual "
                   "audio cables, see docs/google-meet/GOOGLE_MEET_BOT.md); default TARA_MEET_AUDIO")
    j.add_argument("--half-duplex", action="store_true", default=None, help="silence the meeting audio while Tara speaks, so she cannot hear "
                   "her own voice come back from someone's speaker (use this when a phone or laptop speaker is being used); you cannot "
                   "interrupt her by voice")
    j.add_argument("--headless", action="store_true", default=None, help="no visible window (not recommended for first tests)")
    j.add_argument("--per-participant", action="store_true", help="tap each participant's own audio channel and name it from Meet's "
                   "speaking glow, so people are told apart instead of being heard as one unknown mix (see "
                   "docs/google-meet/SPEAKER_BINDING.md); falls back to the mixed tap until a channel arrives")
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
                                address_only=args.address_only, transcripts=args.transcripts, audio=args.audio, half_duplex=args.half_duplex, reply_to_all=args.reply_to_all, camera=not args.no_camera, model_wake=args.model_wake, record=args.record, gain=not args.no_gain, follow_up_s=args.follow_up, per_participant=args.per_participant))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
