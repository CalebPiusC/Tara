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

MEMORY_KINDS = ("fact", "preference", "project", "decision", "correction", "note")
MEMORY_STATUSES = ("confirmed", "candidate", "superseded")
MAX_MEMORY_CHARS = 4000
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
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class MemoryScope:
    """The authorization boundary for a memory. Never build this from model text."""

    tenant_id: str
    user_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not self.user_id.strip():
            raise ValueError("user_id is required")


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
        if self.kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind {self.kind!r}; choose from {list(MEMORY_KINDS)}")
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
class MemoryHit:
    item: MemoryItem
    score: float
    reason: str = ""                 # e.g. "fts", "keyword", "recent"; diagnostics only


def is_expired(item: MemoryItem, now: datetime | None = None) -> bool:
    return item.expires_at is not None and item.expires_at <= (now or utcnow())
