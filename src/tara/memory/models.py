"""Persistent memory records: separate from live conversation state and from notes.

A record is owned by exactly one `MemoryScope` (tenant + user). `session_id` and
`meeting_id` are provenance, never an authorization boundary. A record without a
source is invalid: the store can always say where a memory came from.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .kinds import RECORD_KINDS, normalize_kind

MEMORY_KINDS = RECORD_KINDS
MEMORY_STATUSES = ("confirmed", "candidate", "superseded")
MAX_MEMORY_CHARS = 4000
MEETING_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def meeting_slug(name: str) -> str:
    """A stable id from a human meeting name: 'Join Meeting #2' -> 'join-meeting-2'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64].strip("-")
    return slug or "default"


_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(["a", "an", "the", "is", "are", "was", "were", "of", "to", "and", "or", "in", "on", "for",
                   "with", "do", "does", "it", "this", "that", "i", "you", "we", "our", "my", "me", "what",
                   "how", "can", "be", "as", "at", "from", "by", "about"])


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_text(text: str) -> str:
    """Whitespace/case-normalized form used only for duplicate detection."""
    return " ".join(text.lower().split())


def tokens(text: str) -> set[str]:
    """Content words for matching. Numbers are kept even when short: 'meeting 1' and 'meeting 2' are not the same."""
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and (len(w) > 1 or w.isdigit())}


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class MemoryScope:
    """The authorization boundary for a memory. Never build this from model text."""

    tenant_id: str
    user_id: str
    meeting_id: str | None = None      # when set, reads and writes stay inside this meeting; never from model text

    def __post_init__(self) -> None:
        if not self.tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not self.user_id.strip():
            raise ValueError("user_id is required")
        if self.meeting_id is not None and not MEETING_ID.match(self.meeting_id):
            raise ValueError("meeting_id must be a short lowercase slug (letters, digits, dashes)")


@dataclass(frozen=True)
class MemoryItem:
    id: str
    scope: MemoryScope
    kind: str
    text: str
    source: str                      # e.g. "local-voice-demo:turn:4" or "explicit:local-voice-demo:turn:4"
    session_id: str = ""
    meeting_id: str | None = None
    status: str = "confirmed"        # an explicit user request is a confirmed memory
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    expires_at: datetime | None = None
    confidence: float = 1.0
    supersedes_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("memory id is required")
        canonical = normalize_kind(self.kind)          # accepts "action_item" from older rows and model output
        if canonical != self.kind:
            object.__setattr__(self, "kind", canonical)
        if self.status not in MEMORY_STATUSES:
            raise ValueError(f"unknown memory status {self.status!r}")
        body = self.text.strip()
        if not body:
            raise ValueError("a memory needs text")
        if len(body) > MAX_MEMORY_CHARS:
            raise ValueError(f"a memory may be at most {MAX_MEMORY_CHARS} characters")
        if not self.source.strip():
            raise ValueError("a memory needs provenance (source)")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.scope.tenant_id,
            "user_id": self.scope.user_id,
            "kind": self.kind,
            "text": self.text,
            "source": self.source,
            "session_id": self.session_id,
            "meeting_id": self.meeting_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "confidence": self.confidence,
            "supersedes_id": self.supersedes_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MeetingSession:
    """One numbered meeting inside a section (chat). `session_id` links it to the records written during it.

    `ended_at is None` means the meeting is unfinished (it may still be running). `interrupted` marks a meeting
    that was closed because its process did not end it, so recovery never pretends it completed normally.
    """

    number: int
    session_id: str
    started_at: datetime
    ended_at: datetime | None = None
    interrupted: bool = False
    owner_pid: int = 0
    owner_host: str = ""


@dataclass(frozen=True)
class MemoryHit:
    item: MemoryItem
    score: float
    reason: str = ""                 # e.g. "fts", "keyword", "recent"; diagnostics only


def is_expired(item: MemoryItem, now: datetime | None = None) -> bool:
    return item.expires_at is not None and item.expires_at <= (now or utcnow())
