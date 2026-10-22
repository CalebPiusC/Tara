"""The Meet controller: link handling, interview sessions, and the rule that only one worker joins a given meeting.

Creating a Meet link needs the Google Meet REST API with OAuth (scope `meetings.space.created`, `POST /v2/spaces`). That is not set
up, so for now the owner creates the meeting in Meet (or Calendar) and passes the link; `create_space` says so plainly.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import ConfigError
from .worker import parse_meet_link


class DuplicateWorker(ConfigError):
    """Another worker already holds this meeting."""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class Lease:
    path: Path
    meet_code: str
    session_id: str
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            data = json.loads(self.path.read_text())
            if data.get("session_id") == self.session_id:      # never remove a lock someone else took over
                self.path.unlink()
        except (OSError, ValueError):
            pass


@dataclass
class InterviewSession:
    session_id: str
    interview_code: str
    meet_code: str
    url: str
    started_at: float
    state: str = "created"
    detail: str = ""
    lease: Lease | None = field(default=None, repr=False)


class MeetController:
    def __init__(self, lock_dir: str | Path, *, clock: Callable[[], float] = time.time,
                 pid: Callable[[], int] = os.getpid, alive: Callable[[int], bool] = _pid_alive, stale_s: float = 6 * 3600):
        self.lock_dir = Path(lock_dir)
        self._clock, self._pid, self._alive, self.stale_s = clock, pid, alive, stale_s
        self.sessions: dict[str, InterviewSession] = {}

    @staticmethod
    def parse(link: str) -> tuple[str, str]:
        try:
            return parse_meet_link(link)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

    @staticmethod
    def create_space() -> tuple[str, str]:
        raise ConfigError("Creating a Meet link needs Google OAuth for the Meet REST API (scope meetings.space.created); that is not "
                          "set up yet. Create the meeting in Google Meet or Calendar and pass its link.")

    def acquire(self, meet_code: str, session_id: str) -> Lease:
        """Take the one lock for this meeting. A lock whose process is gone (or very old) is stale and is replaced."""
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        path = self.lock_dir / f"{meet_code}.lock"
        body = json.dumps({"pid": self._pid(), "session_id": session_id, "at": self._clock()})
        for _ in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    held = json.loads(path.read_text())
                except (OSError, ValueError):
                    held = {}
                fresh = self._clock() - float(held.get("at", 0)) < self.stale_s
                if held.get("session_id") == session_id and held.get("pid") == self._pid():
                    return Lease(path, meet_code, session_id)
                if fresh and self._alive(int(held.get("pid", 0))):
                    raise DuplicateWorker(f"another worker (process {held.get('pid')}) is already in meeting {meet_code}") from None
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(body)
            return Lease(path, meet_code, session_id)
        raise DuplicateWorker(f"could not take the lock for meeting {meet_code}")

    def open_session(self, session_id: str, interview_code: str, link: str) -> InterviewSession:
        meet_code, url = self.parse(link)
        for s in self.sessions.values():
            if s.meet_code == meet_code and s.state not in ("ended", "failed", "rejected"):
                raise DuplicateWorker(f"meeting {meet_code} already has a session ({s.session_id})")
        s = InterviewSession(session_id, interview_code, meet_code, url, self._clock())
        s.lease = self.acquire(meet_code, session_id)
        s.state = "joining"
        self.sessions[session_id] = s
        return s

    def close_session(self, session_id: str, state: str = "ended", detail: str = "") -> None:
        s = self.sessions.get(session_id)
        if s is None:
            return
        s.state, s.detail = state, detail
        if s.lease is not None:
            s.lease.release()
            s.lease = None
