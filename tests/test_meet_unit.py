"""Meet bot logic without a browser: link parsing, locks, the join/watch state machine, the adapter's event and audio rules."""
import asyncio
import json
import os

import pytest

from tara.contracts import MeetingEventKind, MeetingTarget, PCMChunk
from tara.errors import ConfigError, JoinError
from tara.meet import selectors as sel
from tara.meet.adapter import MeetAdapter
from tara.meet.controller import DuplicateWorker, MeetController
from tara.meet.worker import MeetWorker, State, WorkerConfig, parse_meet_link

run = asyncio.run


# ---- links and locks -----------------------------------------------------------------------------------------------
def test_meet_links_and_codes_are_parsed_and_everything_else_refused():
    assert parse_meet_link("https://meet.google.com/abc-defg-hij") == ("abc-defg-hij", "https://meet.google.com/abc-defg-hij")
    assert parse_meet_link("https://meet.google.com/abc-defg-hij?authuser=1")[0] == "abc-defg-hij"
    assert parse_meet_link("ABC-DEFG-HIJ")[1] == "https://meet.google.com/abc-defg-hij"
    for bad in ("", "http://meet.google.com/abc-defg-hij", "https://evil.example/meet.google.com/abc-defg-hij",
                "https://meet.google.com/abc", "https://zoom.us/j/123", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            parse_meet_link(bad)
    with pytest.raises(ConfigError, match="OAuth"):
        MeetController.create_space()


def test_only_one_worker_can_hold_a_meeting_and_stale_locks_are_replaced(tmp_path):
    now = [1000.0]
    ctrl = MeetController(tmp_path, clock=lambda: now[0], pid=lambda: 111, alive=lambda pid: pid == 111)
    a = ctrl.open_session("s1", "alex", "abc-defg-hij")
    assert a.state == "joining" and (tmp_path / "abc-defg-hij.lock").exists()
    other = MeetController(tmp_path, clock=lambda: now[0], pid=lambda: 222, alive=lambda pid: pid == 111)
    with pytest.raises(DuplicateWorker):
        other.open_session("s2", "alex", "https://meet.google.com/abc-defg-hij")        # another process, holder alive
    with pytest.raises(DuplicateWorker):
        ctrl.open_session("s3", "alex", "abc-defg-hij")                                  # same process, second session for it
    assert ctrl.open_session("s9", "x", "zzz-yyyy-xxx").meet_code == "zzz-yyyy-xxx"      # a different meeting is fine
    ctrl.close_session("s1", "ended")
    assert not (tmp_path / "abc-defg-hij.lock").exists()
    other.open_session("s2", "alex", "abc-defg-hij")                                     # free again
    dead = MeetController(tmp_path, clock=lambda: now[0], pid=lambda: 333, alive=lambda pid: False)
    dead.sessions.clear()
    now[0] += 10
    assert dead.acquire("abc-defg-hij", "s5").session_id == "s5"                         # holder process is gone: taken over
    stale = tmp_path / "old-aaaa-bbb.lock"
    stale.write_text(json.dumps({"pid": 111, "session_id": "x", "at": 0}))
    assert MeetController(tmp_path, clock=lambda: 10**9, pid=lambda: 5, alive=lambda pid: True).acquire("old-aaaa-bbb", "s6")


def test_a_release_never_removes_a_lock_that_someone_else_took(tmp_path):
    c1 = MeetController(tmp_path, pid=lambda: 1, alive=lambda p: False)
    lease = c1.acquire("abc-defg-hij", "s1")
    c2 = MeetController(tmp_path, pid=lambda: 2, alive=lambda p: False)
    c2.acquire("abc-defg-hij", "s2")                                                     # replaced (holder looked dead)
    lease.release()
    assert json.loads((tmp_path / "abc-defg-hij.lock").read_text())["session_id"] == "s2"


# ---- the state machine with a scripted page --------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    async def sleep(self, d):
        self.t += d
        await asyncio.sleep(0)


class FakePage:
    """Scripted: `screens` is a list of (visible text, set of visible selectors) used one per poll, the last one repeating."""

    def __init__(self, screens, names=None, raw=None):
        self.screens, self.i, self.names, self.clicked, self.filled = list(screens), 0, names or [], [], []
        self.closed = False
        self.raw = raw or FakeRaw()

    def _screen(self):
        return self.cur if hasattr(self, "cur") else self.screens[0]

    async def goto(self, url):
        self.url = url

    async def text(self):
        self.cur = self.screens[min(self.i, len(self.screens) - 1)]     # one screen per poll; visible()/click() see the same one
        self.i += 1
        return self.cur[0]

    async def visible(self, selector):
        return selector in self._screen()[1]

    async def click(self, selector):
        self.clicked.append(selector)
        return selector in self._screen()[1]

    async def click_text(self, labels):
        for lab in labels:
            if f"btn:{lab}" in self._screen()[1]:
                self.clicked.append(lab)
                return lab
        return ""

    async def fill_name(self, name):
        self.filled.append(name)
        return True

    async def participants(self):
        return list(self.names)

    def is_closed(self):
        return self.closed


class FakeRaw:
    def __init__(self):
        self.played, self.stopped, self.queued = [], 0, 0

    async def evaluate(self, script, arg=None):
        if "__taraPlay" in script:
            self.played.append(arg)
            self.queued = 150
            return self.queued
        if "__taraStop" in script:
            self.stopped += 1
            return 321
        if "__taraStatus" in script:
            q, self.queued = self.queued, 0                  # drains after the first poll
            return {"queuedMs": q}
        return None


PRE = ("ready to join", {"btn:join now", sel.CAMERA_TOGGLE_ON})
ASK = ("ready to join", {"btn:ask to join"})
WAIT = ("asking to be let in... someone will let you in soon", set())
INCALL = ("in the call", {sel.IN_CALL})
DENIED = ("you can't join this video call", set())
ENDED = ("you left the meeting", set())


def worker_for(pages, cfg=None, clock=None):
    clock = clock or Clock()
    launched: list = []
    it = iter(pages)
    closed: list = []

    async def launcher():
        page = next(it)
        launched.append(page)

        async def close():
            closed.append(page)
        return page, close

    c = cfg or WorkerConfig(clock=clock, sleep=clock.sleep, poll_s=0.5, join_timeout_s=10, admit_timeout_s=30,
                            reconnect_backoff_s=0.1, max_reconnects=2)
    return MeetWorker("https://meet.google.com/abc-defg-hij", launcher, c), launched, closed


async def drain(w, n=None):
    out = []
    while not w.events.empty():
        out.append(w.events.get_nowait())
    return out


def test_join_happy_path_turns_the_camera_off_fills_a_guest_name_and_clicks_join():
    async def go():
        page = FakePage([PRE, PRE, INCALL])
        w, launched, closed = worker_for([page])
        w.start()
        assert await w.wait_for(State.IN_MEETING, timeout=5) is State.IN_MEETING
        assert "join now" in page.clicked and sel.CAMERA_TOGGLE_ON in page.clicked and page.filled == ["Tara"]
        await w.leave()
        assert w.state is State.ENDED and closed == [page] and sel.LEAVE_BUTTON in page.clicked
        await w.leave()                                                                   # second leave is harmless
        states = [e.state for e in await drain(w) if e.kind == "state"]
        assert states[:3] == [State.LAUNCHING, State.JOINING, State.IN_MEETING] and states[-1] is State.ENDED
    run(go())


def test_waiting_room_is_respected_then_admitted():
    async def go():
        page = FakePage([ASK, ASK, WAIT, WAIT, WAIT, INCALL])
        w, *_ = worker_for([page])
        w.start()
        await w.wait_for(State.WAITING_ROOM, timeout=5)
        assert w.state is State.WAITING_ROOM and "ask to join" in page.clicked
        await w.wait_for(State.IN_MEETING, timeout=5)
        assert w.state is State.IN_MEETING
        await w.leave()
    run(go())


def test_not_admitted_in_time_is_rejected_and_the_browser_is_closed():
    async def go():
        page = FakePage([ASK, ASK, WAIT])
        w, launched, closed = worker_for([page])
        w.start()
        assert await w.wait_for(State.IN_MEETING, timeout=500) is State.REJECTED
        assert "not admitted within 30s" in w.detail
        await asyncio.sleep(0.01)
        assert closed == [page]
    run(go())


@pytest.mark.parametrize("screens,final,needle", [
    ([PRE, PRE, DENIED], State.REJECTED, "did not admit"),
    ([("", set()), ("", set()), DENIED], State.REJECTED, "refused this visitor"),
    ([PRE, PRE, ENDED], State.ENDED, "has ended"),
    ([("sign in to continue", set())], State.FAILED, "not signed in"),
    ([("something unexpected", set())], State.FAILED, "could not find a join button"),
    ([PRE, PRE, ("a screen nobody planned for", set())], State.FAILED, "recognised screen"),
])
def test_unknown_and_bad_screens_end_honestly(screens, final, needle):
    async def go():
        w, *_ = worker_for([FakePage(screens)])
        w.start()
        assert await w.wait_for(State.IN_MEETING, timeout=500) is final
        assert needle in w.detail
    run(go())


def test_people_changes_are_reported_once_and_self_is_ignored():
    async def go():
        page = FakePage([INCALL], names=["Tara (You)", "Alex Kim"])
        w, *_ = worker_for([page])
        w.start()
        await w.wait_for(State.IN_MEETING, timeout=5)
        await asyncio.sleep(0.05)
        for _ in range(5):
            await asyncio.sleep(0)
        page.names = ["Tara (You)", "Alex Kim", "Tara", "Sam"]                          # the bot's own name is ignored too
        await asyncio.sleep(0.05)
        page.names = ["Alex Kim"]
        await asyncio.sleep(0.05)
        people = [e for e in await drain(w) if e.kind == "participants"]
        assert [e.joined for e in people][:2] == [("Alex Kim",), ("Sam",)]
        assert any(e.left == ("Sam",) for e in people) and len(people) == 3             # repeated polls added nothing
        await w.leave()
    run(go())


def test_a_lost_call_is_rejoined_with_a_new_browser_and_gives_up_after_the_limit():
    async def go():
        lost = ("", set())
        pages = [FakePage([INCALL, lost]), FakePage([PRE, INCALL])]
        w, launched, closed = worker_for(pages)
        w.start()
        await w.wait_for(State.IN_MEETING, timeout=5)
        await asyncio.sleep(0.3)
        evs = [e for e in await drain(w) if e.kind == "state"]
        assert any(e.state is State.RECONNECTING for e in evs)
        assert any(e.state is State.IN_MEETING and e.reconnected for e in evs) and len(launched) == 2 and pages[0] in closed
        await w.leave()
        bad = [FakePage([INCALL, lost]) for _ in range(5)]
        w2, launched2, _ = worker_for(bad)
        w2.start()
        assert await w2.wait_for(State.FAILED, timeout=5) is State.FAILED
        assert "gave up after 2 reconnect attempts" in w2.detail and len(launched2) == 3
    run(go())


def test_a_page_that_dies_is_treated_as_a_lost_call():
    async def go():
        p1 = FakePage([INCALL])
        w, launched, _ = worker_for([p1, FakePage([PRE, INCALL])])
        w.start()
        await w.wait_for(State.IN_MEETING, timeout=5)
        p1.closed = True
        await asyncio.sleep(0.3)
        assert w.reconnects == 1 and len(launched) == 2
        await w.leave()
    run(go())


def test_leaving_while_still_joining_cancels_and_cleans_up():
    async def go():
        page = FakePage([ASK, ASK, WAIT])
        w, launched, closed = worker_for([page], cfg=WorkerConfig(poll_s=0.01, join_timeout_s=60, admit_timeout_s=60))
        w.start()
        await w.wait_for(State.WAITING_ROOM, timeout=5)
        await w.leave()
        assert w.state is State.ENDED and closed == [page]
        with pytest.raises(RuntimeError):
            w.start() if w._task is not None else (_ for _ in ()).throw(RuntimeError())
    run(go())


# ---- the adapter -------------------------------------------------------------------------------------------------------
def adapter_for(tmp_path, pages, session="s1", **kw):
    clock = Clock()
    ctrl = MeetController(tmp_path / "locks")
    it = iter(pages)

    def factory(_bridge):
        async def launcher():
            page = next(it)

            async def close():
                return None
            return page, close
        return launcher

    cfg = WorkerConfig(clock=clock, sleep=clock.sleep, poll_s=0.5, join_timeout_s=10, admit_timeout_s=30, reconnect_backoff_s=0.1)
    status: list[str] = []
    kw.setdefault("interview_code", "alex")
    ad = MeetAdapter(ctrl, factory, session_id=session, url="https://meet.google.com/abc-defg-hij",
                     candidate_stream_id="a", worker_cfg=cfg, on_status=status.append, **kw)
    return ad, ctrl, status


async def gather(ad, bucket):
    async for e in ad.events():
        bucket.append(e)


def test_one_other_person_is_the_candidate_two_or_more_are_an_unknown_mix(tmp_path):
    async def go():
        page = FakePage([INCALL], names=[])
        greeted: list[int] = []

        async def first():
            greeted.append(1)
        ad, ctrl, status = adapter_for(tmp_path, [page], on_first_participant=first)
        await ad.join(MeetingTarget())                                                   # the URL comes from the adapter itself
        events: list = []
        pump = asyncio.create_task(gather(ad, events))
        ad._on_remote_audio(b"\x01\x00" * 160)
        await asyncio.sleep(0.01)
        assert events[-1].payload["chunk"].stream_id == "mix"                             # nobody else yet
        page.names = ["Alex Kim"]
        await asyncio.sleep(0.1)
        ad._on_remote_audio(b"\x01\x00" * 160)
        await asyncio.sleep(0.01)
        joined = [e for e in events if e.kind is MeetingEventKind.PARTICIPANT_JOINED]
        assert joined[0].payload == {"id": "a", "name": "Alex Kim"} and events[-1].payload["chunk"].stream_id == "a"
        page.names = ["Alex Kim", "Sam"]
        await asyncio.sleep(0.1)
        ad._on_remote_audio(b"\x01\x00" * 160)
        await asyncio.sleep(0.01)
        assert events[-1].payload["chunk"].stream_id == "mix" and any("cannot be told apart" in s for s in status)
        page.names = ["Alex Kim"]
        await asyncio.sleep(0.1)
        page.names = []                                                                  # the candidate leaves, comes back
        await asyncio.sleep(0.1)
        page.names = ["Alex Kim"]
        await asyncio.sleep(0.1)
        assert greeted == [1]                                                            # the greeting fires once, not on every join
        assert len({e.event_id for e in events}) == len(events)
        await ad.leave()
        await pump
        assert not list((tmp_path / "locks").glob("*.lock"))
    run(go())


def test_ending_the_meeting_ends_the_event_stream_and_frees_the_lock(tmp_path):
    async def go():
        page = FakePage([INCALL, ENDED])
        ad, ctrl, status = adapter_for(tmp_path, [page])
        await ad.join(MeetingTarget())
        events: list = []
        await asyncio.wait_for(gather(ad, events), 5)                                     # the stream finishes by itself
        assert events[-1].kind is MeetingEventKind.ENDED and events[-1].payload["reason"] == "ended"
        await ad.leave()
        assert not list((tmp_path / "locks").glob("*.lock")) and ctrl.sessions["s1"].state == "ended"
    run(go())


def test_join_failures_raise_and_release_the_meeting(tmp_path):
    async def go():
        for screens, word in (([PRE, PRE, DENIED], "rejected"), ([("sign in", set())], "failed")):
            ad, ctrl, _ = adapter_for(tmp_path, [FakePage(screens)])
            with pytest.raises(JoinError, match=word):
                await ad.join(MeetingTarget())
            assert not list((tmp_path / "locks").glob("*.lock"))
    run(go())


def test_a_second_adapter_for_the_same_meeting_never_launches_a_browser(tmp_path):
    async def go():
        first, ctrl, _ = adapter_for(tmp_path, [FakePage([INCALL])])
        await first.join(MeetingTarget())
        launched: list = []

        def factory(_b):
            launched.append(1)
            raise AssertionError("must not launch")
        second = MeetAdapter(ctrl, factory, session_id="s2", url="https://meet.google.com/abc-defg-hij")
        with pytest.raises(DuplicateWorker):
            await second.join(MeetingTarget())
        assert launched == []
        await first.leave()
    run(go())


async def chunks_of(items):
    for c in items:
        yield c


def test_speaking_stopping_and_the_not_in_meeting_cases(tmp_path):
    async def go():
        raw = FakeRaw()
        page = FakePage([INCALL], raw=raw)
        ad, ctrl, _ = adapter_for(tmp_path, [page])
        idle = await ad.send_audio("early", chunks_of([PCMChunk(b"\x00\x00" * 480, 24000, 0)]))
        assert idle.completed is False and raw.played == []                               # not in the meeting: nothing is said
        await ad.join(MeetingTarget())
        r = await ad.send_audio("u1", chunks_of([PCMChunk(b"\x01\x00" * 2400, 24000, 0)] * 3))
        assert r.completed and r.delivered_ms == 300 and len(raw.played) == 3
        assert await ad.stop_audio("u1") == 0                                             # nothing is playing: nothing to cancel
        assert (await ad.send_audio("u1", chunks_of([PCMChunk(b"\x01\x00" * 480, 24000, 0)]))).completed is False   # a stopped id stays stopped
        # stop during playback: the in-page cancel runs and its played time is reported
        gate = asyncio.Event()

        async def slow():
            yield PCMChunk(b"\x01\x00" * 2400, 24000, 0)
            await gate.wait()
            yield PCMChunk(b"\x01\x00" * 2400, 24000, 0)
        task = asyncio.create_task(ad.send_audio("u2", slow()))
        await asyncio.sleep(0.05)
        assert await ad.stop_audio("u2") == 321 and raw.stopped == 1
        gate.set()
        assert (await task).completed is False
        await ad.leave()
        assert (await ad.send_audio("late", chunks_of([PCMChunk(b"\x00\x00" * 480, 24000, 0)]))).completed is False
    run(go())


def test_the_bridge_ignores_bad_frames_and_validates_what_it_plays():
    from tara.meet.bridge import AudioBridge
    got: list[bytes] = []
    b = AudioBridge(got.append)
    b._receive("not base64!!")
    b._receive("AQ==")                                                                    # one byte: not whole samples
    b._receive("AQABAA==")                                                                # two samples
    assert got == [b"\x01\x00\x01\x00"] and b.bad_frames == 2 and b.frames_in == 1

    async def go():
        class P:
            async def evaluate(self, *a):
                return 5
        assert await AudioBridge.play(P(), b"", 24000) == 0
        for bad in ((b"\x00", 24000), (b"\x00\x00", 100), (b"\x00\x00" * 300_000, 24000)):
            with pytest.raises(ValueError):
                await AudioBridge.play(P(), *bad)
    run(go())


def test_the_command_line_refuses_bad_input_without_opening_a_browser(tmp_path, capsys, monkeypatch):
    from tara.meet.run import check, join
    from tara.settings import RuntimeSettings
    env = {"TARA_LIVE_ENABLED": "1", "GEMINI_API_KEY": "k", "TARA_LIVE_MODEL": "m", "TARA_WORK_ENABLED": "1",
           "TARA_WORK_DIR": str(tmp_path / "w"), "TARA_MEET_LOCK_DIR": str(tmp_path / "locks"), "TARA_MEET_PROFILE_DIR": str(tmp_path / "p")}
    s = RuntimeSettings.from_env(env)
    assert run(join(s, "https://zoom.us/j/1", "alex", None)) == 2
    assert run(join(s, "abc-defg-hij", "nobody", None)) == 2 and "interview new" in capsys.readouterr().err
    monkeypatch.delenv("TARA_CHROME_PATH", raising=False)
    assert check(s) in (0, 1) and "Bot profile folder" in capsys.readouterr().out
    off = RuntimeSettings.from_env({**env, "TARA_WORK_ENABLED": "0"})
    assert run(join(off, "abc-defg-hij", "alex", None)) == 2
    assert os.path.exists(tmp_path) and not list((tmp_path / "locks").glob("*.lock")) if (tmp_path / "locks").exists() else True


class StatusRaw(FakeRaw):
    def __init__(self, status):
        super().__init__()
        self._status = status

    async def evaluate(self, script, arg=None):
        if "__taraStatus" in script:
            return self._status
        return await super().evaluate(script, arg)


def test_the_bot_says_when_it_hears_the_meeting_and_warns_when_the_audio_route_is_dead(tmp_path):
    async def go():
        page = FakePage([INCALL], names=["Alex Kim"], raw=StatusRaw({"remoteTracks": 0, "gumCalls": 0, "inRms": 0, "queuedMs": 0}))
        ad, ctrl, status = adapter_for(tmp_path, [page], diag_s=0.05, warn_after_s=0.1)
        ad._t0 = 0.0
        await ad.join(MeetingTarget())
        ad._on_remote_audio(b"\x00\x00" * 160)                                       # silence: not "hearing" yet
        assert not any("hearing the meeting" in s for s in status)
        ad._on_remote_audio(b"\xff\x3f" * 160)                                       # loud
        ad._on_remote_audio(b"\xff\x3f" * 160)
        assert sum("hearing the meeting" in s for s in status) == 1                    # said once
        for _ in range(40):
            await asyncio.sleep(0.05)
            if any("Meet has not opened the microphone" in s for s in status):
                break
        assert any("no incoming audio track was captured" in s for s in status) and any("has not opened the microphone" in s for s in status)
        assert any(s.startswith("audio check:") for s in status) and any("people in the meeting: Alex Kim" in s for s in status)
        await ad.leave()
    run(go())


def test_a_normal_meeting_needs_no_interview_and_answers_by_default():
    from tara.config import AppPolicy, Mode, Role, SpeakingPolicy
    from tara.config import validate as validate_config
    from tara.meet.run import _meet_meeting, _parser
    reply = _meet_meeting(reply_to_all=True)
    named = _meet_meeting(reply_to_all=False)
    assert reply.mode is Mode.NATURAL and reply.policy is SpeakingPolicy.PROACTIVE_ALLOWED and named.policy is SpeakingPolicy.ON_ADDRESS_ONLY
    assert all(not validate_config(c, AppPolicy()).errors() for c in (reply, named)) and reply.participants[0].role is Role.ORGANIZER
    args = _parser().parse_args(["join", "abc-defg-hij", "--address-only", "--transcripts"])
    assert args.interview == "" and args.address_only and args.transcripts and args.section


def test_icon_font_text_and_duplicates_are_not_people():
    from tara.meet.browser import real_names
    assert real_names(["frame_person", "keep_outline", "Feranmi Oresajo", "beyond social", "Feranmi Oresajo", "", "  "]) == [
        "Feranmi Oresajo", "beyond social"]


class WireRaw(StatusRaw):
    def __init__(self, status):
        super().__init__(status)
        self.packets = 0

    async def evaluate(self, script, arg=None):
        if "__taraStats" in script:
            self.packets += 100
            return {"connections": 2, "inPackets": self.packets, "inBytes": self.packets * 120, "outPackets": 4, "tracks": 3}
        return await super().evaluate(script, arg)


def _diag_messages(tmp_path, wire_peak, tap_peak):
    async def go():
        raw = WireRaw({"remoteTracks": 3, "gumCalls": 3, "inRms": 0, "peakRms": tap_peak, "wirePeak": wire_peak, "queuedMs": 0,
                       "tapInfo": [{"muted": False, "state": "live", "enabled": True, "paused": False}]})
        ad, ctrl, status = adapter_for(tmp_path, [FakePage([INCALL], names=["Alex"], raw=raw)], diag_s=0.03, warn_after_s=0.05)
        await ad.join(MeetingTarget())
        await asyncio.sleep(0.4)
        await ad.leave()
        return status
    return run(go())


def test_the_diagnostics_tell_silence_in_the_meeting_from_a_broken_tap(tmp_path):
    silent = _diag_messages(tmp_path, 0.0, 0.0)
    assert any("Meet's packets report silence" in s for s in silent) and any("on the wire: 100 audio packets" in s for s in silent)
    assert any(s.startswith("tapped tracks: #1 live, live, playing") for s in silent) and not any("tap is silent" in s for s in silent)
    broken = _diag_messages(tmp_path, 0.35, 0.0)
    assert any("bridge tap is silent" in s for s in broken) and not any("report silence" in s for s in broken)
    fine = _diag_messages(tmp_path, 0.35, 0.3)
    assert not any(s.startswith("WARNING") for s in fine)


def test_detail_goes_to_the_run_log_and_not_the_screen(tmp_path):
    async def go():
        raw = WireRaw({"remoteTracks": 3, "gumCalls": 3, "inRms": 0, "peakRms": 0, "wirePeak": 0, "queuedMs": 0, "tapInfo": []})
        lines: list[str] = []
        ad, ctrl, status = adapter_for(tmp_path, [FakePage([INCALL], names=["Alex"], raw=raw)], diag_s=0.03, warn_after_s=99, log=lines.append)
        await ad.join(MeetingTarget())
        await asyncio.sleep(0.2)
        await ad.leave()
        return lines, status
    lines, status = run(go())
    assert any(line.startswith("debug ") for line in lines) and any(line.startswith("status ") for line in lines)
    assert not any(s.startswith("debug ") for s in status)


# ---- virtual audio cables (devices mode) -----------------------------------------------------------------------------
class FakeSD:
    """A tiny stand-in for the sounddevice module."""

    def __init__(self, names):
        self.devs = [{"name": n, "max_input_channels": 2, "max_output_channels": 2, "default_samplerate": 48000.0} for n in names]
        self.opened: list[tuple[str, dict]] = []

    def query_devices(self, device=None, kind=None):
        if device is None and kind is None:
            return self.devs
        return self.devs[device if device is not None else 0]

    def RawInputStream(self, **kw):
        self.opened.append(("in", kw))
        return object()

    def RawOutputStream(self, **kw):
        self.opened.append(("out", kw))
        return object()


def test_the_sound_backend_picks_named_cables_and_copies_mono_to_stereo():
    from tara.adapters.audio_device import SoundDeviceBackend, expand_channels
    sd = FakeSD(["MacBook Pro Microphone", "BlackHole 2ch", "BlackHole 16ch"])
    be = SoundDeviceBackend("BlackHole 2ch", "blackhole 16CH", sd_module=sd)                  # names match loosely
    be.open_input(callback=None, samplerate=16000, channels=1, dtype="int16", blocksize=480)
    be.open_output(samplerate=48000, channels=2, dtype="int16")
    assert sd.opened[0][1]["device"] == 1 and sd.opened[1][1]["device"] == 2 and be.default_output_rate() == 48000
    with pytest.raises(JoinError, match="no input audio device matching 'Nope'"):
        SoundDeviceBackend("Nope", sd_module=sd)
    assert expand_channels(b"\x01\x00\x02\x00", 2) == b"\x01\x00\x01\x00\x02\x00\x02\x00" and expand_channels(b"\x01\x00", 1) == b"\x01\x00"


def test_the_cable_check_says_what_is_missing():
    from tara.meet.devices import VirtualAudio, check_devices
    both = check_devices(VirtualAudio(), FakeSD(["BlackHole 2ch", "BlackHole 16ch"]))
    assert all(ok for _, ok, _ in both)
    half = check_devices(VirtualAudio(), FakeSD(["MacBook Pro Microphone", "BlackHole 2ch"]))
    assert [ok for _, ok, _ in half] == [True, False]


def test_devices_mode_needs_two_different_cables_and_page_mode_is_the_default():
    from tara.settings import RuntimeSettings, validate
    base = {"TARA_LIVE_ENABLED": "1", "GEMINI_API_KEY": "k", "TARA_LIVE_MODEL": "m"}
    s = RuntimeSettings.from_env(base)
    assert s.meet.audio == "page" and not [i for i in validate(s) if i.field.startswith("TARA_MEET")]
    same = RuntimeSettings.from_env({**base, "TARA_MEET_AUDIO": "devices", "TARA_MEET_IN_DEVICE": "BlackHole 2ch", "TARA_MEET_OUT_DEVICE": "BlackHole 2ch"})
    assert any(i.field == "TARA_MEET_IN_DEVICE" and i.severity == "error" for i in validate(same))
    bad = RuntimeSettings.from_env({**base, "TARA_MEET_AUDIO": "speaker"})
    assert any(i.field == "TARA_MEET_AUDIO" for i in validate(bad))


def test_the_bridge_script_carries_the_cable_names_only_in_devices_mode():
    from tara.meet.bridge import bridge_js
    from tara.meet.browser import CHROME_ARGS, CHROME_ARGS_DEVICES
    page = bridge_js()
    cab = bridge_js("devices", {"mic": "BlackHole 16ch", "speaker": "BlackHole 2ch"})
    assert "__taraDevices" not in page.split("(() =>")[0] and cab.startswith('window.__taraMode = "devices"') and "BlackHole 16ch" in cab
    assert "--mute-audio" in CHROME_ARGS and "--mute-audio" not in CHROME_ARGS_DEVICES       # muting would silence the cable too


def test_devices_mode_listens_to_the_meeting_cable_and_speaks_to_the_voice_cable(tmp_path):
    async def go():
        from test_audio_device_adapter import FakeBackend

        from tara.adapters.audio_device import AudioDeviceAdapter
        backend = FakeBackend(output_rate=48000)
        inner = AudioDeviceAdapter(backend, stream_id="you", out_channels=2)
        raw = FakeRaw()
        page = FakePage([INCALL], names=[], raw=raw)
        ad, ctrl, status = adapter_for(tmp_path, [page], audio=inner, diag_s=0)
        await ad.join(MeetingTarget())
        events: list = []
        pump = asyncio.create_task(gather(ad, events))
        backend.inputs[0].feed(b"\xff\x3f" * 480)                                        # loud audio on the meeting cable
        await asyncio.sleep(0.05)
        assert events[-1].kind is MeetingEventKind.AUDIO and events[-1].payload["chunk"].stream_id == "mix"   # nobody known yet
        assert any("hearing the meeting" in s for s in status)
        page.names = ["Alex Kim"]
        await asyncio.sleep(0.1)
        backend.inputs[0].feed(b"\xff\x3f" * 480)
        await asyncio.sleep(0.05)
        assert events[-1].payload["chunk"].stream_id == "a"                              # one other person: the candidate
        r = await ad.send_audio("u1", chunks_of([PCMChunk(b"\x01\x00" * 480, 24000, 0)]))
        out = backend.outputs[-1]
        assert r.completed and out.kw["channels"] == 2 and len(out.writes[0]) == len(b"\x01\x00" * 480) * 2 * 2   # resampled to 48 kHz and doubled
        assert raw.played == []                                                          # nothing goes through the page in this mode
        assert any(s.startswith("Tara spoke for") for s in status)
        await ad.leave()
        await pump
        assert backend.inputs[0].closed
    run(go())


def test_missing_cables_fail_before_a_browser_is_started(tmp_path):
    async def go():
        from test_audio_device_adapter import FakeBackend

        from tara.adapters.audio_device import AudioDeviceAdapter
        backend = FakeBackend(fail_output=True)
        launched: list = []
        ctrl = MeetController(tmp_path / "locks")

        def factory(_b):
            launched.append(1)
            raise AssertionError("must not launch")
        ad = MeetAdapter(ctrl, factory, session_id="s1", url="https://meet.google.com/abc-defg-hij", audio=AudioDeviceAdapter(backend))
        with pytest.raises(JoinError):
            await ad.join(MeetingTarget())
        assert launched == [] and not list((tmp_path / "locks").glob("*.lock"))
    run(go())


def test_half_duplex_silences_the_meeting_while_tara_speaks_and_for_a_moment_after(tmp_path):
    async def go():
        from test_audio_device_adapter import FakeBackend

        from tara.adapters.audio_device import AudioDeviceAdapter

        def loud(ev):
            data = ev.payload["chunk"].data
            return any(data)

        for half, expect_silent in ((True, True), (False, False)):
            backend = FakeBackend(output_rate=24000)
            inner = AudioDeviceAdapter(backend, stream_id="you")
            ad, ctrl, status = adapter_for(tmp_path, [FakePage([INCALL], names=["Alex"])], audio=inner, diag_s=0, half_duplex=half, tail_s=0.3,
                                           session=f"s-{half}")
            await ad.join(MeetingTarget())
            events: list = []
            pump = asyncio.create_task(gather(ad, events))
            await asyncio.sleep(0.1)

            async def slow():
                yield PCMChunk(b"\x01\x00" * 2400, 24000, 0)
                await asyncio.sleep(0.4)
                yield PCMChunk(b"\x01\x00" * 2400, 24000, 0)
            task = asyncio.create_task(ad.send_audio("u", slow()))
            await asyncio.sleep(0.15)
            backend.inputs[0].feed(b"\xff\x3f" * 480)                                    # her own voice coming back while she speaks
            await asyncio.sleep(0.05)
            during = [e for e in events if e.kind is MeetingEventKind.AUDIO][-1]
            await task
            backend.inputs[0].feed(b"\xff\x3f" * 480)                                    # and just after she stops (the echo is late)
            await asyncio.sleep(0.05)
            after = [e for e in events if e.kind is MeetingEventKind.AUDIO][-1]
            await asyncio.sleep(0.4)
            backend.inputs[0].feed(b"\xff\x3f" * 480)                                    # later: a real person speaking again
            await asyncio.sleep(0.05)
            later = [e for e in events if e.kind is MeetingEventKind.AUDIO][-1]
            assert (not loud(during) and not loud(after)) is expect_silent
            assert loud(later)                                                           # the tail ends, so people can be heard again
            assert (ad.silenced_frames > 0) is expect_silent
            await ad.leave()
            await pump
    run(go())


def test_a_normal_meeting_keeps_one_audio_label_however_many_people_come_and_go(tmp_path):
    async def go():
        page = FakePage([INCALL], names=[])
        ad, ctrl, status = adapter_for(tmp_path, [page], interview_code="", diag_s=0)
        await ad.join(MeetingTarget())
        events: list = []
        pump = asyncio.create_task(gather(ad, events))
        labels = []
        for names in ([], ["Alex"], ["Alex", "Sam"], [], ["Sam"]):
            page.names = names
            await asyncio.sleep(0.15)
            ad._on_remote_audio(b"\x01\x00" * 160)
            await asyncio.sleep(0.01)
            labels.append(events[-1].payload["chunk"].stream_id)
        assert set(labels) == {"a"}                                                      # never flips to "mix" and back
        await ad.leave()
        await pump
    run(go())


def test_a_flickering_people_list_does_not_cause_join_and_leave_events():
    async def go():
        flicker = [["Alex"], [], ["Alex"], ["Alex"], [], ["Alex"], ["Alex"], ["Alex"]]
        page = FakePage([INCALL], names=["Alex"])
        calls = {"n": 0}
        real = page.participants

        async def participants():
            i = calls["n"]
            calls["n"] += 1
            return list(flicker[i]) if i < len(flicker) else ["Alex"]
        page.participants = participants
        w, *_ = worker_for([page])
        w.start()
        await w.wait_for(State.IN_MEETING, timeout=500)
        for _ in range(60):
            await asyncio.sleep(0.01)
        people = [e for e in await drain(w) if e.kind == "participants"]
        assert [e.joined for e in people] == [("Alex",)] and not any(e.left for e in people)   # one stable join, no phantom leaves
        await w.leave()
        assert real is not None
    run(go())


def test_the_adapter_reports_how_many_people_are_in_the_room(tmp_path):
    async def go():
        page = FakePage([INCALL], names=[])
        counts: list[int] = []
        ad, ctrl, status = adapter_for(tmp_path, [page], interview_code="", diag_s=0, on_people=counts.append)
        await ad.join(MeetingTarget())
        events: list = []
        pump = asyncio.create_task(gather(ad, events))
        for names in (["Alex"], ["Alex", "Sam"], ["Sam"]):
            page.names = names
            await asyncio.sleep(0.2)
        assert counts[0] == 1 and 2 in counts and counts[-1] == 1
        await ad.leave()
        await pump
    run(go())


def test_the_answer_policy_follows_the_head_count_unless_fixed():
    from tara.config import SpeakingPolicy as P
    from tara.meet.run import speaking_policy
    assert speaking_policy(1, "auto") is P.PROACTIVE_ALLOWED
    assert speaking_policy(2, "auto") is P.ON_ADDRESS_ONLY and speaking_policy(5, "auto") is P.ON_ADDRESS_ONLY
    assert speaking_policy(5, "all") is P.PROACTIVE_ALLOWED and speaking_policy(1, "address") is P.ON_ADDRESS_ONLY


def test_the_newer_meet_link_form_with_a_meet_segment_is_accepted():
    from tara.meet.worker import parse_meet_link
    assert parse_meet_link("https://meet.google.com/meet/dfr-dqbq-ein") == ("dfr-dqbq-ein", "https://meet.google.com/dfr-dqbq-ein")


def test_the_camera_picture_is_built_and_injected_only_when_asked(tmp_path):
    from PIL import Image

    from tara.meet.bridge import bridge_js, make_picture
    img = tmp_path / "p.png"
    Image.new("RGB", (200, 300), (200, 100, 50)).save(img)
    pic = make_picture(img, "Hey Tara, I am here to help")
    assert pic.startswith("data:image/jpeg;base64,")
    assert "__taraPicture" in bridge_js("devices", {"mic": "a", "speaker": "b"}, pic) and "__taraPicture =" not in bridge_js()


def test_a_link_pasted_with_a_doubled_scheme_or_no_scheme_is_accepted():
    from tara.meet.worker import parse_meet_link
    want = ("cky-unfz-bpr", "https://meet.google.com/cky-unfz-bpr")
    assert parse_meet_link("https://https://meet.google.com/cky-unfz-bpr") == want
    assert parse_meet_link("meet.google.com/cky-unfz-bpr") == want


def test_recorder_writes_aligned_valid_wav_files_and_survives_failure(tmp_path):
    import wave

    from tara.meet.recorder import MeetingRecorder
    now = [0.0]
    rec = MeetingRecorder(tmp_path / "r", "abc", clock=lambda: now[0])
    one_second = b"\x01\x00" * 16000
    rec.write_room(one_second, 16000)
    now[0] = 3.0
    rec.write_tara(b"\x02\x00" * 8000, 16000)                   # Tara starts talking 2.5 s in: the track is padded to the same timeline
    rec.write_room(one_second, 16000)
    rec.close()
    with wave.open(str(rec.room.path)) as w:
        assert w.getframerate() == 16000 and w.getnframes() == 48000 and w.getnchannels() == 1
    with wave.open(str(rec.tara.path)) as w:
        assert w.getnframes() == 48000                                  # same timeline as the room track
    rec.room.fh = None
    rec.room.rate = 0
    bad = MeetingRecorder(tmp_path / "x", "b")
    bad.room.path = tmp_path / "missing" / "no.wav"            # cannot be opened
    bad.write_room(one_second, 16000)
    assert bad.failed and bad.write_tara(one_second, 16000) is None
