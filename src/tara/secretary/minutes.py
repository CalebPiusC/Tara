"""Meeting minutes from recorded facts. The meeting memory is the source of truth; nothing here invents content.

Confirmed items (explicit promotion) and unconfirmed candidates are always kept apart, because a statement in the
transcript is a claim, not a decision.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..voice.memory import MeetingMemory, MemoryRecord, Scope, WorkingMemory


@dataclass(frozen=True)
class Minutes:
    title: str
    started: str
    attendees: tuple[str, ...]
    decisions: tuple[MemoryRecord, ...]
    actions: tuple[MemoryRecord, ...]
    open_questions: tuple[MemoryRecord, ...]
    unconfirmed: tuple[MemoryRecord, ...]
    proposals: tuple[MemoryRecord, ...] = ()
    summaries: tuple[MemoryRecord, ...] = ()
    excerpt: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def empty(self) -> bool:
        return not (self.decisions or self.actions or self.open_questions or self.unconfirmed or self.proposals
                    or self.summaries)


def collect_minutes(memory: MeetingMemory, scope: Scope, *, title: str = "Meeting minutes",
                    attendees: Iterable[str] = (), working: WorkingMemory | None = None, excerpt_turns: int = 12,
                    started: str = "") -> Minutes:
    records = memory.list(scope)
    confirmed = [r for r in records if r.status == "confirmed"]
    candidates = [r for r in records if r.status == "candidate"]
    return Minutes(
        title=title, started=started or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        attendees=tuple(dict.fromkeys(a for a in attendees if a)),
        decisions=tuple(r for r in confirmed if r.kind == "decision"),
        actions=tuple(r for r in confirmed if r.kind == "action_item"),
        open_questions=tuple(r for r in records if r.kind == "open_question"),
        unconfirmed=tuple(r for r in candidates if r.kind in ("decision", "action_item")),
        proposals=tuple(r for r in records if r.kind == "proposal"),
        summaries=tuple(r for r in records if r.kind == "summary"),
        excerpt=tuple(list(working.turns)[-excerpt_turns:]) if working is not None else ())


def _line(r: MemoryRecord) -> str:
    extra = []
    if r.owner:
        extra.append(f"owner: {r.owner}")
    if r.deadline:
        extra.append(f"deadline: {r.deadline}")
    if r.confirmed_by:
        extra.append(f"confirmed by: {r.confirmed_by}")
    return r.body + (f" ({'; '.join(extra)})" if extra else "")


def minutes_facts(m: Minutes, *, include_excerpt: bool = True) -> dict[str, Any]:
    """The only content the text model receives for a minutes document."""
    facts: dict[str, Any] = {
        "Meeting details": f"Title: {m.title}. Started: {m.started}.",
        "Attendees": ", ".join(m.attendees) or "Not recorded",
        "Confirmed decisions": [_line(r) for r in m.decisions] or ["None recorded"],
        "Confirmed action items": [_line(r) for r in m.actions] or ["None recorded"],
        "Open questions": [_line(r) for r in m.open_questions] or ["None recorded"],
        "Proposals (not agreed)": [_line(r) for r in m.proposals] or ["None"],
        "Unconfirmed items (said in the meeting but never confirmed)": [f"{r.kind}: {_line(r)}" for r in m.unconfirmed] or ["None"],
    }
    if m.summaries:
        facts["Recorded summary notes"] = [_line(r) for r in m.summaries]
    if include_excerpt and m.excerpt:
        facts["Recent conversation (verbatim, for context only)"] = [f"{s}: {t}" for s, t in m.excerpt]
    return facts
