"""Bound the number of concurrent voice sessions and refuse duplicate ids."""
from __future__ import annotations

from ..core.errors import DuplicateSession, SessionLimitExceeded


class SessionRegistry:
    def __init__(self, max_sessions: int):
        if max_sessions < 1:
            raise ValueError("max_sessions must be >= 1")
        self.max_sessions = max_sessions
        self._active: set[str] = set()

    def acquire(self, session_id: str) -> None:
        if session_id in self._active:
            raise DuplicateSession(session_id)
        if len(self._active) >= self.max_sessions:
            raise SessionLimitExceeded(f"limit {self.max_sessions} reached")
        self._active.add(session_id)

    def release(self, session_id: str) -> None:
        self._active.discard(session_id)

    @property
    def active(self) -> int:
        return len(self._active)
