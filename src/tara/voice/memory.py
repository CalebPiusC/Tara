"""Four distinct memory concerns, kept apart on purpose (docs/MEMORY_AND_RAG.md).

1. Live conversation state:  `tara.state.ConversationState` (per session, transient).
2. Working memory:           `WorkingMemory` (bounded recent turns + summary, per session).
3. Meeting memory:           `MeetingMemory` Protocol; `InMemoryMeetingMemory` is a stand-in.
4. Long-term knowledge:      `KnowledgeProvider` (tara.contracts), bound to a session scope by `scoped_search`.

Isolation: everything is keyed by `Scope(tenant_id, meeting_id)`; a store never answers for another scope.
No database or vector store exists; this is the interface plus an in-memory implementation.
"""
from __future__ import annotations

import itertools
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

from ..contracts import KnowledgeProvider, Passage
from ..errors import PermissionDenied
from ..tools import ToolDef

NOTE_KINDS = ("decision", "action_item", "open_question", "summary")
PROMOTABLE_KINDS = ("decision", "action_item")


@dataclass(frozen=True)
class Scope:
    tenant_id: str
    meeting_id: str


@dataclass
class WorkingMemory:
    """Immediate context for coherent replies. Per session; never shared."""

    max_turns: int = 40
    summary: str = ""
    turns: deque[tuple[str, str]] = field(default_factory=deque)   # (speaker, text)
    pending_questions: list[str] = field(default_factory=list)
    total_turns: int = 0                                          # ever added; lets a recorder know what is new

    def add_turn(self, speaker: str, text: str) -> None:
        self.turns.append((speaker, text))
        self.total_turns += 1
        while len(self.turns) > self.max_turns:
            self.turns.popleft()

    def render(self, max_chars: int = 2000) -> str:
        """Compact text used to re-seed a fresh provider session after a reconnect (newest kept)."""
        lines = [f"{s}: {t}" for s, t in self.turns]
        body = "\n".join(lines)
        if len(body) > max_chars:
            body = body[-max_chars:]
        head = f"Summary so far: {self.summary}\n" if self.summary else ""
        return (head + "Recent conversation:\n" + body).strip()


@dataclass(frozen=True)
class MemoryRecord:
    kind: str                         # decision | action_item | open_question | summary
    body: str
    source_event_ids: tuple[str, ...]  # provenance is mandatory
    status: str = "candidate"          # never "confirmed" without an explicit promotion
    owner: str = ""                    # optional, exactly as stated by a participant
    deadline: str = ""                 # optional, exactly as stated by a participant
    confirmed_by: str = ""             # participant id that ran the explicit promotion


class MeetingMemory(Protocol):
    def add(self, scope: Scope, rec: MemoryRecord) -> None: ...
    def list(self, scope: Scope, kind: str | None = None) -> list[MemoryRecord]: ...
    def promote_latest(self, scope: Scope, kinds: tuple[str, ...] = PROMOTABLE_KINDS,
                       confirmed_by: str = "") -> MemoryRecord | None: ...


class InMemoryMeetingMemory:
    def __init__(self) -> None:
        self._data: dict[Scope, list[MemoryRecord]] = {}

    def add(self, scope: Scope, rec: MemoryRecord) -> None:
        if not rec.source_event_ids:
            raise ValueError("a memory record needs provenance (source_event_ids)")
        if rec.status != "candidate":
            raise ValueError("new records are candidates; promotion is a separate, explicit action")
        if rec.kind not in NOTE_KINDS:
            raise ValueError(f"unknown note kind {rec.kind!r}; choose from {list(NOTE_KINDS)}")
        if not rec.body.strip():
            raise ValueError("a memory record needs a body")
        self._data.setdefault(scope, []).append(rec)

    def list(self, scope: Scope, kind: str | None = None) -> list[MemoryRecord]:
        return [r for r in self._data.get(scope, []) if kind is None or r.kind == kind]

    def promote_latest(self, scope: Scope, kinds: tuple[str, ...] = PROMOTABLE_KINDS,
                       confirmed_by: str = "") -> MemoryRecord | None:
        """Promote the newest candidate of `kinds`. Provenance is preserved; only this method sets status."""
        records = self._data.get(scope, [])
        for i in range(len(records) - 1, -1, -1):
            rec = records[i]
            if rec.kind in kinds and rec.status == "candidate":
                promoted = replace(rec, status="confirmed", confirmed_by=confirmed_by)
                records[i] = promoted
                return promoted
        return None


def summarise_agreed(memory: MeetingMemory, scope: Scope,
                     kinds: tuple[str, ...] = NOTE_KINDS) -> tuple[list[MemoryRecord], list[MemoryRecord]]:
    """Read-only summary: confirmed items first, candidates listed separately. It invents nothing."""
    records = [r for r in memory.list(scope) if r.kind in kinds]
    return ([r for r in records if r.status == "confirmed"], [r for r in records if r.status == "candidate"])


def record_note_tool(memory: MeetingMemory, scope: Scope, active: Callable[[], bool], *,
                     source: str = "session", timeout_s: float = 5.0) -> ToolDef:
    """`record_note`: a candidate note, never a confirmed fact. The scope is bound here, not by the model.

    Side-effecting (it writes memory) but it may run during a live turn because the spoken notes mode is
    the authorization and the handler re-checks it. Provenance is the tool call sequence for this session.
    """
    calls = itertools.count(1)

    def handler(kind: str, body: str, owner: str = "", deadline: str = "") -> dict[str, str]:
        if not active():
            raise PermissionDenied("notes mode is not active")
        text = body.strip()
        if kind not in NOTE_KINDS:
            raise ValueError(f"unknown note kind {kind!r}")
        if not text:
            raise ValueError("empty note")
        rec = MemoryRecord(kind, text[:500], (f"{source}:note:{next(calls)}",),
                           owner=owner.strip()[:80], deadline=deadline.strip()[:80])
        memory.add(scope, rec)
        return {"status": "candidate", "kind": kind}

    return ToolDef("record_note", {"kind": str, "body": str}, handler,
                   optional={"owner": str, "deadline": str},
                   description="Record a candidate decision, action item, open question or summary. "
                               "Record only what was actually said; never invent details.",
                   timeout_s=timeout_s, permission="notes_write", side_effect=True, live_write=True)


def scoped_search(knowledge: KnowledgeProvider, scope: Scope):
    """Bind retrieval to the session scope. The model never supplies the scope; this closure is what a
    `search_knowledge` tool calls. Real authorization lives in the retrieval store (docs/MEMORY_AND_RETRIEVAL_SECURITY.md);
    the in-memory provider used today has none and must not hold multi-tenant data."""
    async def search(query: str, k: int = 3) -> list[Passage]:
        return await knowledge.search(query[:300], k)
    return search
