"""Agenda and time-keeping. In-process state for the running meeting; nudges are spoken through the normal gate.

The clock is pure (`tick` returns the nudges that are due), so it is testable without waiting. Each nudge is sent once.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

PENDING, CURRENT, DONE, SKIPPED = "pending", "current", "done", "skipped"
MAX_ITEMS = 12


@dataclass
class AgendaItem:
    title: str
    minutes: int | None = None
    state: str = PENDING
    started_at: float | None = None


@dataclass
class Agenda:
    clock: Callable[[], float] = time.monotonic
    items: list[AgendaItem] = field(default_factory=list)
    total_minutes: int | None = None
    started_at: float | None = None

    def set(self, titles: list[str], total_minutes: int | None = None) -> None:
        clean = [" ".join(t.split())[:120] for t in titles if t.strip()][:MAX_ITEMS]
        if not clean:
            raise ValueError("an agenda needs at least one item")
        self.items = [AgendaItem(t) for t in clean]
        self.total_minutes = total_minutes if total_minutes and 1 <= total_minutes <= 600 else None
        self.started_at = self.clock()
        self.items[0].state, self.items[0].started_at = CURRENT, self.clock()
        if self.total_minutes:                       # split the time evenly as a starting plan
            each = max(1, self.total_minutes // len(self.items))
            for it in self.items:
                it.minutes = each

    def _find(self, which: str) -> AgendaItem | None:
        w = which.strip().lower()
        digits = "".join(c for c in w if c.isdigit())
        if digits and 1 <= int(digits) <= len(self.items):
            return self.items[int(digits) - 1]
        if w in ("current", "this", "it", ""):
            return next((i for i in self.items if i.state == CURRENT), None)
        return next((i for i in self.items if w in i.title.lower()), None)

    def mark(self, which: str, state: str = DONE) -> AgendaItem | None:
        item = self._find(which)
        if item is None or state not in (DONE, SKIPPED, CURRENT):
            return None
        item.state = state
        if state in (DONE, SKIPPED):
            nxt = next((i for i in self.items if i.state == PENDING), None)
            if nxt is not None and not any(i.state == CURRENT for i in self.items):
                nxt.state, nxt.started_at = CURRENT, self.clock()
        elif state == CURRENT:
            item.started_at = self.clock()
        return item

    def elapsed_min(self) -> float:
        return 0.0 if self.started_at is None else (self.clock() - self.started_at) / 60

    def status(self) -> dict[str, object]:
        cur = next((i for i in self.items if i.state == CURRENT), None)
        out: dict[str, object] = {
            "items": [{"n": n + 1, "title": i.title, "state": i.state, **({"planned_minutes": i.minutes} if i.minutes else {})}
                      for n, i in enumerate(self.items)],
            "current": cur.title if cur else "", "covered": sum(1 for i in self.items if i.state == DONE),
            "remaining": sum(1 for i in self.items if i.state in (PENDING, CURRENT)), "elapsed_minutes": round(self.elapsed_min(), 1)}
        if self.total_minutes:
            out["minutes_left"] = round(self.total_minutes - self.elapsed_min(), 1)
        return out


class MeetingClock:
    """Turns the agenda and elapsed time into quiet nudges: 5 minutes left, 1 minute left, time up, an item running long."""

    def __init__(self, agenda: Agenda, speak: Callable[[str], Awaitable[None]] | None = None, *, max_nudges: int = 6,
                 poll_s: float = 15.0):
        self.agenda, self.speak, self.max_nudges, self.poll_s = agenda, speak, max_nudges, poll_s
        self._sent: set[str] = set()

    def tick(self) -> list[str]:
        a = self.agenda
        if not a.items or a.started_at is None or len(self._sent) >= self.max_nudges:
            return []
        out: list[str] = []
        if a.total_minutes:
            left = a.total_minutes - a.elapsed_min()
            for key, limit, text in (("5", 5, "About five minutes left."), ("1", 1, "One minute left."),
                                     ("0", 0, "We are at the planned end time.")):
                if left <= limit and key not in self._sent:
                    self._sent.add(key)
                    out.append(text)
                    if key != "0":
                        pending = [i.title for i in a.items if i.state in (PENDING, CURRENT)]
                        if pending:
                            out[-1] += " Still on the agenda: " + ", ".join(pending[:3]) + "."
                    break
        cur = next((i for i in a.items if i.state == CURRENT and i.minutes and i.started_at is not None), None)
        if cur is not None and not out:
            spent = (a.clock() - cur.started_at) / 60  # type: ignore[operator]
            key = f"over:{cur.title}"
            planned = cur.minutes or 0
            if planned and spent >= planned * 1.5 and spent - planned >= 2 and key not in self._sent:
                self._sent.add(key)
                out.append(f"We have spent about {round(spent)} minutes on {cur.title}, over the {planned} planned.")
        return out

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.poll_s)
            for text in self.tick():
                if self.speak is not None:
                    try:
                        await self.speak(text)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - a missed nudge is not worth breaking the meeting
                        pass
