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
    ad = MeetAdapter(ctrl, factory, session_id=session, interview_code="alex", url="https://meet.google.com/abc-defg-hij",
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
