"""The browser worker: drives Chrome through Playwright to join one Google Meet as the bot, and watches it.

Rules it keeps: it joins the normal way (the pre-join screen), it waits in the waiting room until a person admits it (never tries to
get around that), it leaves when told, and it reports honestly what it sees. The state machine below only talks to `MeetPage`, a
small facade, so it is tested with a fake page and, separately, with a real Chrome against a local stand-in for Meet.

States: idle -> launching -> joining -> waiting_room -> in_meeting; in_meeting <-> reconnecting; ending in ended, rejected or failed.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from . import selectors as sel


class State(str, Enum):
    IDLE = "idle"
    LAUNCHING = "launching"
    JOINING = "joining"
    WAITING_ROOM = "waiting_room"
    IN_MEETING = "in_meeting"
    RECONNECTING = "reconnecting"
    ENDED = "ended"
    REJECTED = "rejected"
    FAILED = "failed"


TERMINAL = {State.ENDED, State.REJECTED, State.FAILED}
MEET_URL = re.compile(r"^https://meet\.google\.com/(?:lookup/|meet/)?([a-z]{3}-[a-z]{4}-[a-z]{3})(?:[/?#].*)?$", re.I)
CODE = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$", re.I)


def parse_meet_link(value: str) -> tuple[str, str]:
    """Accept a Meet link or a bare code ('abc-defg-hij'). Returns (code, url). Raises ValueError for anything else."""
    v = value.strip()
    while v.lower().startswith(("https://https://", "https://http://")):          # a pasted link with the scheme typed twice
        v = v[len("https://"):]
    if v.lower().startswith("meet.google.com/"):
        v = "https://" + v
    if CODE.match(v):
        return v.lower(), f"https://meet.google.com/{v.lower()}"
    m = MEET_URL.match(v)
    if not m:
        raise ValueError("that is not a Google Meet link; expected https://meet.google.com/abc-defg-hij or the code abc-defg-hij")
    return m.group(1).lower(), f"https://meet.google.com/{m.group(1).lower()}"


@dataclass(frozen=True)
class WorkerEvent:
    kind: str                     # "state" | "participants"
    state: State | None = None
    detail: str = ""
    reconnected: bool = False
    joined: tuple[str, ...] = ()
    left: tuple[str, ...] = ()
    names: tuple[str, ...] = ()


class MeetPage(Protocol):
    """Everything the state machine needs from a page. Playwright-backed in production, faked in tests."""

    async def goto(self, url: str) -> None: ...
    async def text(self) -> str: ...                                  # visible text, lower case, bounded
    async def visible(self, selector: str) -> bool: ...
    async def click(self, selector: str) -> bool: ...
    async def click_text(self, labels: tuple[str, ...]) -> str: ...   # clicks the first visible button whose text or label matches; returns it
    async def fill_name(self, name: str) -> bool: ...
    async def participants(self) -> list[str]: ...
    def is_closed(self) -> bool: ...


Launcher = Callable[[], Awaitable[tuple[MeetPage, Callable[[], Awaitable[None]]]]]


@dataclass
class WorkerConfig:
    bot_name: str = "Tara"
    join_timeout_s: float = 60.0         # time to reach any recognised screen after clicking join
    admit_timeout_s: float = 600.0       # how long to wait in the waiting room for a person to let the bot in
    poll_s: float = 1.0
    max_reconnects: int = 3
    reconnect_backoff_s: float = 2.0
    people_stable_polls: int = 2         # a change in the people list must be seen this many polls in a row (the list flickers while it redraws)
    camera_on: bool = False              # leave the camera on at the pre-join screen (it shows Tara's picture instead of video)
    gone_polls: int = 3                  # polls without the in-call controls before the call counts as lost
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)


class MeetWorker:
    def __init__(self, url: str, launcher: Launcher, cfg: WorkerConfig | None = None):
        self.url = url
        self.launcher = launcher
        self.cfg = cfg or WorkerConfig()
        self.state = State.IDLE
        self.detail = ""
        self.events: asyncio.Queue[WorkerEvent] = asyncio.Queue()
        self.page: MeetPage | None = None
        self.reconnects = 0
        self.ever_in_meeting = False                 # true once the bot was admitted, even if the meeting ended right after
        self.people: set[str] = set()
        self._seen: set[str] | None = None
        self._seen_n = 0
        self._close: Callable[[], Awaitable[None]] | None = None
        self._task: asyncio.Task[None] | None = None
        self._talking: tuple[str, ...] = ()
        self._stop = asyncio.Event()

    # ---- control -------------------------------------------------------------------------------------------
    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("worker already started")                 # one worker per instance; the controller prevents duplicates
        self._task = asyncio.create_task(self._run(), name="meet-worker")

    async def wait_for(self, *states: State, timeout: float = 30.0) -> State:
        deadline = self.cfg.clock() + timeout
        while self.state not in states and self.state not in TERMINAL:
            if self.cfg.clock() > deadline:
                raise TimeoutError(f"still {self.state.value} after {timeout:.0f}s")
            await self.cfg.sleep(0.05)
        return self.state

    async def leave(self) -> None:
        """Leave the call politely, then close the browser. Safe to call twice and from any state."""
        self._stop.set()
        page = self.page
        if page is not None and self.state is State.IN_MEETING and not page.is_closed():
            try:
                await page.click(sel.LEAVE_BUTTON)
            except Exception:  # noqa: BLE001 - closing the browser also leaves the call
                pass
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self._close_browser()
        if self.state not in TERMINAL:
            self._set(State.ENDED, "left")

    # ---- internals -------------------------------------------------------------------------------------------
    def _set(self, state: State, detail: str = "", *, reconnected: bool = False) -> None:
        if state is self.state and detail == self.detail:
            return
        self.state, self.detail = state, detail
        if state is State.IN_MEETING:
            self.ever_in_meeting = True
        self.events.put_nowait(WorkerEvent("state", state, detail, reconnected))

    async def _close_browser(self) -> None:
        close, self._close, self.page = self._close, None, None
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001 - cleanup must finish
                pass

    async def _open(self) -> MeetPage:
        self._set(State.LAUNCHING)
        page, close = await self.launcher()
        self.page, self._close = page, close
        await page.goto(self.url)
        return page

    async def _run(self) -> None:
        reconnected = False
        try:
            while not self._stop.is_set():
                try:
                    page = await self._open()
                    outcome = await self._join(page, reconnected)
                    if outcome is not State.IN_MEETING:
                        return                                              # rejected, ended or failed: terminal, already set
                    outcome = await self._watch(page)
                    if outcome in TERMINAL:
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - a browser failure is reported and retried within limits
                    self.detail = f"{type(exc).__name__}"
                if self._stop.is_set():
                    return
                # lost the call: try again, bounded
                await self._close_browser()
                if self.reconnects >= self.cfg.max_reconnects:
                    self._set(State.FAILED, f"gave up after {self.reconnects} reconnect attempts")
                    return
                self.reconnects += 1
                self._set(State.RECONNECTING, f"attempt {self.reconnects} of {self.cfg.max_reconnects}")
                await self.cfg.sleep(self.cfg.reconnect_backoff_s * self.reconnects)
                reconnected = True
        except asyncio.CancelledError:
            raise
        finally:
            if self.state in TERMINAL or self._stop.is_set():
                await self._close_browser()

    async def _join(self, page: MeetPage, reconnected: bool) -> State:
        self._set(State.JOINING)
        c = self.cfg
        deadline = c.clock() + c.join_timeout_s
        for _ in range(20):                                                  # dismiss pop-ups and reach the pre-join screen
            text = await page.text()
            if await page.visible(sel.IN_CALL):                              # already in (a reconnect that kept the call)
                self._set(State.IN_MEETING, "joined", reconnected=reconnected)
                return State.IN_MEETING
            if any(t in text for t in sel.DENIED_TEXT):
                self._set(State.REJECTED, "the host or the meeting did not allow the bot to join")
                return State.REJECTED
            if any(t in text for t in sel.ENDED_TEXT):
                self._set(State.ENDED, "the meeting has ended")
                return State.ENDED
            if await page.click_text(sel.DISMISS_BUTTONS):
                continue
            break
        await page.fill_name(c.bot_name)                                     # only filled when Meet asks for a name (guest)
        if not c.camera_on and await page.visible(sel.CAMERA_TOGGLE_ON):
            await page.click(sel.CAMERA_TOGGLE_ON)                           # camera off: audio-first, no video sent
        clicked = ""
        text = ""
        for _ in range(8):                                                   # the page may still be loading: look a few times
            clicked = await page.click_text(sel.JOIN_BUTTONS)
            if clicked:
                break
            text = await page.text()
            if any(t in text for t in sel.DENIED_TEXT):
                self._set(State.REJECTED, "Meet refused this visitor before it could ask to join. Sign the bot's Google account in "
                                          "(`./dev.sh meet login`; check with `./dev.sh meet probe <link>`) and make sure the meeting "
                                          "lets that account in")
                return State.REJECTED
            await c.sleep(c.poll_s)
        if not clicked:
            if any(t in text for t in sel.SIGN_IN_TEXT):
                self._set(State.FAILED, "the bot is not signed in; run `./dev.sh meet login` once")
            else:
                self._set(State.FAILED, "could not find a join button on the pre-join screen (the page may have changed)")
            return State.FAILED
        waiting_since: float | None = None
        while True:
            if self._stop.is_set() or page.is_closed():
                raise RuntimeError("page closed while joining")
            text = await page.text()
            if await page.visible(sel.IN_CALL):
                self._set(State.IN_MEETING, "joined", reconnected=reconnected)
                return State.IN_MEETING
            if any(t in text for t in sel.DENIED_TEXT):
                self._set(State.REJECTED, "the host did not admit the bot")
                return State.REJECTED
            if any(t in text for t in sel.ENDED_TEXT):
                self._set(State.ENDED, "the meeting has ended")
                return State.ENDED
            if any(t in text for t in sel.WAITING_TEXT):
                if waiting_since is None:
                    waiting_since = c.clock()
                    self._set(State.WAITING_ROOM, "waiting for a person to let the bot in")
                if c.clock() - waiting_since > c.admit_timeout_s:
                    self._set(State.REJECTED, f"not admitted within {c.admit_timeout_s:.0f}s")
                    return State.REJECTED
            elif waiting_since is None and c.clock() > deadline:
                self._set(State.FAILED, "joining did not reach a recognised screen (the page may have changed)")
                return State.FAILED
            await c.sleep(c.poll_s)

    async def _watch(self, page: MeetPage) -> State:
        """In the meeting: report people coming and going; notice that the call ended, was lost, or the page died."""
        c = self.cfg
        gone = 0
        opened_people = False
        while not self._stop.is_set():
            if page.is_closed():
                raise RuntimeError("page closed")
            text = await page.text()
            if any(t in text for t in sel.DENIED_TEXT):
                self._set(State.REJECTED, "the bot was removed from the meeting")
                return State.REJECTED
            in_call = await page.visible(sel.IN_CALL)
            if not in_call:
                if any(t in text for t in sel.ENDED_TEXT):
                    self._set(State.ENDED, "the meeting has ended")
                    return State.ENDED
                gone += 1
                if gone >= c.gone_polls:
                    raise RuntimeError("lost the call")
            else:
                gone = 0
                if not opened_people:
                    opened_people = True
                    await page.click(sel.PEOPLE_BUTTON)                       # Meet only lists people while this panel is open
                names = {n for n in await page.participants() if n and "(you)" not in n.lower()
                         and n.strip().lower() != c.bot_name.lower()}
                if names == self._seen:
                    self._seen_n += 1
                else:
                    self._seen, self._seen_n = names, 1
                need = c.people_stable_polls if names else c.people_stable_polls + 1      # an empty list is the most likely flicker
                speak = getattr(page, "speaking", None)
                if speak is not None:                                                   # who is talking right now (best effort)
                    talking = tuple(sorted(n for n in await speak() if "(you)" not in n.lower() and n.strip().lower() != c.bot_name.lower()))
                    if talking != self._talking:
                        self._talking = talking
                        self.events.put_nowait(WorkerEvent("speaking", names=talking))
                if names != self.people and self._seen_n >= need:
                    joined, left = tuple(sorted(names - self.people)), tuple(sorted(self.people - names))
                    self.people = names
                    self.events.put_nowait(WorkerEvent("participants", joined=joined, left=left, names=tuple(sorted(names))))
            await c.sleep(c.poll_s)
        return State.ENDED
