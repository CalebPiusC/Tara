"""Recall inside a meeting: "what did we say about X?" and gentle reminders that never speak on their own.

`search_meeting` looks at this meeting's notes and recent conversation. Results say WHAT KIND of evidence each is:
confirmed note, unconfirmed candidate note, or something said in conversation. Nothing is inferred or invented.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from ..tools.executor import ToolDef
from ..voice.memory import MeetingMemory, MemoryRecord, Scope, WorkingMemory

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an the is are was were of to and or in on for with do does it this that i you we our my me what how can be as at from by about "
                  "did say said tell told remember again earlier before just".split())


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


@dataclass(frozen=True)
class Evidence:
    source: str            # confirmed_note | candidate_note | conversation
    kind: str
    text: str
    speaker: str = ""
    score: float = 0.0

    def public(self) -> dict[str, str]:
        out = {"source": self.source, "kind": self.kind, "text": self.text}
        if self.speaker:
            out["speaker"] = self.speaker
        return out


def search_meeting(memory: MeetingMemory, scope: Scope, working: WorkingMemory, query: str, limit: int = 5,
                   *, min_overlap: int = 1) -> list[Evidence]:
    q = _tokens(query)
    if not q:
        return []
    found: list[Evidence] = []
    for r in memory.list(scope):
        overlap = len(q & _tokens(r.body))
        if overlap >= min_overlap:
            src = "confirmed_note" if r.status == "confirmed" else "candidate_note"
            found.append(Evidence(src, r.kind, _line(r), score=overlap + (0.5 if r.status == "confirmed" else 0.0)))
    for speaker, text in working.turns:
        overlap = len(q & _tokens(text))
        if overlap >= max(min_overlap, 1) and speaker != "Tara":
            found.append(Evidence("conversation", "said", text, speaker=speaker, score=overlap * 0.9))
    found.sort(key=lambda e: -e.score)
    return found[: max(1, min(limit, 10))]


def _line(r: MemoryRecord) -> str:
    bits = [b for b in (f"owner: {r.owner}" if r.owner else "", f"deadline: {r.deadline}" if r.deadline else "") if b]
    return r.body + (f" ({'; '.join(bits)})" if bits else "")


def recall_meeting_tool(memory: MeetingMemory, scope: Scope, working: WorkingMemory) -> ToolDef:
    def handler(query: str, limit: int = 5) -> dict[str, object]:
        hits = search_meeting(memory, scope, working, query, limit)
        if not hits:
            return {"found": False, "note": "nothing in this meeting matches; say you have no record of it"}
        return {"found": True, "results": [h.public() for h in hits],
                "note": "confirmed_note is agreed; candidate_note and conversation are only what someone said"}

    return ToolDef("recall_meeting", {"query": str}, handler, optional={"limit": int},
                   description="Search THIS meeting's notes and recent conversation to answer 'what did we say about X' or to "
                               "remind the team of something said earlier. Say whether an item was confirmed or only mentioned. "
                               "If nothing matches, say you have no record; never invent meeting history.",
                   timeout_s=3.0, permission="meeting_read", side_effect=False, category="meeting")


def meeting_recap_tool(memory: MeetingMemory, scope: Scope) -> ToolDef:
    def handler() -> dict[str, object]:
        recs = memory.list(scope)
        confirmed = [r for r in recs if r.status == "confirmed"]
        pending = [r for r in recs if r.status == "candidate"]
        return {"confirmed": [{"kind": r.kind, "text": _line(r)} for r in confirmed][:20],
                "unconfirmed": [{"kind": r.kind, "text": _line(r)} for r in pending][:20],
                "note": "report confirmed items as agreed and unconfirmed items as 'mentioned but not confirmed'"}

    return ToolDef("meeting_recap", {}, handler,
                   description="Get what has been agreed so far in this meeting (confirmed decisions and actions) and what was "
                               "only mentioned. Use for 'summarise what we've agreed'.",
                   timeout_s=3.0, permission="meeting_read", side_effect=False, category="meeting")


class RecallNudger:
    """After a participant turn, look for a related CONFIRMED earlier item and, at most once per item, send the
    engine a quiet context note. It never speaks: whether Tara says anything is still the participation gate's call."""

    def __init__(self, memory: MeetingMemory, scope: Scope, send_note: Callable[[str], None], *,
                 min_overlap: int = 2, max_nudges: int = 5):
        self.memory, self.scope, self.send_note = memory, scope, send_note
        self.min_overlap, self.max_nudges = min_overlap, max_nudges
        self._nudged: set[str] = set()

    def observe(self, text: str) -> str | None:
        if len(self._nudged) >= self.max_nudges:
            return None
        q = _tokens(text)
        best: tuple[int, MemoryRecord] | None = None
        for r in self.memory.list(self.scope):
            if r.status != "confirmed" or r.kind not in ("decision", "action_item"):
                continue
            key = r.source_event_ids[0] if r.source_event_ids else r.body
            if key in self._nudged:
                continue
            overlap = len(q & _tokens(r.body))
            if overlap >= self.min_overlap and (best is None or overlap > best[0]):
                best = (overlap, r)
        if best is None:
            return None
        r = best[1]
        self._nudged.add(r.source_event_ids[0] if r.source_event_ids else r.body)
        note = (f"Earlier in this meeting the team confirmed a {r.kind.replace('_', ' ')}: {_line(r)}. "
                "If it is relevant to what people are discussing now and you are addressed or invited to speak, "
                "you may remind them briefly. Do not interrupt to say it.")
        self.send_note(note)
        return note
