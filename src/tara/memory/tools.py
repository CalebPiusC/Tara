"""Tool definitions for explicit long-term memory.

Writes are code-authorized: the handler checks `MemoryWriteGate`, which only opens
after the participant's final transcript contains an explicit request. The model
cannot choose the tenant, user, session or source.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from ..core.config import ToolSpec
from ..core.errors import PermissionDenied, ToolError
from ..tools.executor import ToolDef
from .intent import MemoryWriteGate
from .models import MAX_MEMORY_CHARS, MeetingSession, MemoryItem, MemoryScope
from .service import MemoryService, span, when


def remember_memory_tool(service: MemoryService, scope: MemoryScope, gate: MemoryWriteGate, *,
                         source: str, session_id: str, timeout_s: float = 5.0) -> ToolDef:
    async def handler(text: str, kind: str = "fact") -> dict[str, str]:
        if not gate.remember_allowed():
            raise PermissionDenied("remember_memory requires an explicit request from the participant")
        item = await service.remember(scope, text.strip()[:MAX_MEMORY_CHARS], source=source, kind=kind, session_id=session_id)
        return {"status": "saved", "id": item.id, "kind": item.kind,
                "say": "Tell the participant in one short sentence that it is saved and that you can bring it up "
                       "whenever they ask, including in a later meeting. Do not stay silent."}

    return ToolDef(
        "remember_memory", {"text": str}, handler, optional={"kind": str},
        description="Save a fact, preference, project detail or decision the participant explicitly asked you to "
                    "remember. Never call this for ordinary conversation. Confirm the save only after this tool "
                    "returns status=saved; if it returns an error, say the save failed.",
        timeout_s=timeout_s, permission="memory_write", side_effect=True, live_write=True)


def recall_memory_tool(service: MemoryService, scope: MemoryScope, *, timeout_s: float = 5.0) -> ToolDef:
    async def sessions_by_id() -> dict[str, MeetingSession]:
        if scope.meeting_id is None:
            return {}
        try:
            return {m.session_id: m for m in await service.sessions(scope)}
        except Exception:  # noqa: BLE001 - dates are a nicety; the memory itself still comes back
            return {}

    def row(item: MemoryItem, known: dict[str, MeetingSession]) -> dict[str, str]:
        out = {"id": item.id, "text": item.text, "kind": item.kind, "source": item.source,
               "status": item.status, "updated_at": item.updated_at.isoformat()}
        m = known.get(item.session_id)
        if m is not None:
            out["meeting"] = str(m.number)
            out["when"] = when(m.started_at)
        return out

    async def handler(query: str = "", limit: int = 5) -> dict[str, object]:
        n = max(1, min(limit, 10))
        known = await sessions_by_id()
        hits = await service.recall(scope, query, limit=n) if query.strip() else []
        if hits:
            return {"count": len(hits), "match": "search", "memories": [row(h.item, known) for h in hits]}
        # Broad questions ("anything we did last week?") share no words with a stored memory, so list the newest.
        recent = await service.store.list_recent(scope, n)
        if recent:
            return {"count": len(recent), "match": "recent",
                    "note": "No exact match. These are the newest stored memories; say honestly whether they answer "
                            "the question, and do not claim dates you cannot see in updated_at.",
                    "memories": [row(i, known) for i in recent]}
        return {"count": 0, "memories": [],
                "note": "Nothing is stored in long-term memory. Tell the participant plainly that you do not "
                        "remember anything yet."}

    return ToolDef(
        "recall_memory", {"query": str}, handler, optional={"limit": int},
        description="Search long-term memory from earlier conversations. Use this whenever the participant asks "
                    "what you remember or refers to something discussed before. Say a short thoughtful \"Hmm, let me check\" first. "
                    "Always answer after the result: if count is 0, say you do not remember anything; never stay silent "
                    "and never invent it.",
        timeout_s=timeout_s, permission="memory_read", side_effect=False)


def forget_memory_tool(service: MemoryService, scope: MemoryScope, gate: MemoryWriteGate, *,
                       timeout_s: float = 5.0) -> ToolDef:
    async def handler(memory_id: str = "", text: str = "") -> dict[str, str]:
        if not gate.forget_allowed():
            raise PermissionDenied("forget_memory requires an explicit request from the participant")
        if memory_id.strip():
            deleted = await service.forget(scope, memory_id.strip())
        elif text.strip():
            deleted = await service.forget_exact(scope, text)
        else:
            raise ValueError("provide memory_id (from recall_memory) or the exact text to forget")
        return {"status": "deleted" if deleted else "not_found"}

    return ToolDef(
        "forget_memory", {}, handler, optional={"memory_id": str, "text": str},
        description="Forget a stored memory after an explicit request. Prefer memory_id from recall_memory; "
                    "otherwise pass the exact text. Report success only when the tool returns status=deleted.",
        timeout_s=timeout_s, permission="memory_write", side_effect=True, live_write=True)


def list_meetings_tool(service: MemoryService, scope: MemoryScope, current_session_id: str, *, timeout_s: float = 5.0) -> ToolDef:
    async def handler() -> dict[str, object]:
        sessions = await service.sessions(scope)
        rows = []
        for m in sorted(sessions, key=lambda m: m.number):
            rows.append({"meeting": m.number, "when": span(m), "current": m.session_id == current_session_id})
        return {"section": scope.meeting_id, "count": len(rows), "meetings": rows,
                "note": "" if rows else "No meetings are recorded in this section yet."}

    return ToolDef(
        "list_meetings", {}, handler,
        description="List the numbered meetings held so far in this section with their dates and times. Say a short "
                    "\"Hmm, let me check\" first, then read out the meeting numbers with dates and times in plain speech.",
        timeout_s=timeout_s, permission="memory_read", side_effect=False)


def meeting_lookup_tool(service: MemoryService, scope: MemoryScope, current_session_id: str, *, timeout_s: float = 8.0) -> ToolDef:
    async def handler(which: str = "last") -> dict[str, object]:
        sessions = sorted(await service.sessions(scope), key=lambda m: m.number)
        earlier = [m for m in sessions if m.session_id != current_session_id]
        w = which.strip().lower()
        target: MeetingSession | None = None
        if w in ("", "last", "previous", "latest", "most recent"):
            target = earlier[-1] if earlier else None
        elif w == "first":
            target = sessions[0] if sessions else None
        else:
            digits = "".join(c for c in w if c.isdigit())
            target = next((m for m in sessions if digits and m.number == int(digits)), None)
        if target is None:
            return {"found": False, "note": "There is no such meeting in this section. Say so plainly; do not guess."}
        items = await service.session_items(scope, target.session_id, 120)

        def pick(*kinds: str, limit: int = 8, size: int = 300) -> list[str]:
            return [i.text[:size] + ("" if i.status == "confirmed" else " (unconfirmed)") for i in items if i.kind in kinds][:limit]

        summary = pick("summary", limit=1, size=900)
        return {"found": True, "meeting": target.number, "when": span(target), "current": target.session_id == current_session_id,
                "summary": summary[0] if summary else "",
                "decisions": pick("decision"), "actions": pick("action"),
                "facts": pick("fact", "preference", "project", "note", "correction"),
                "excerpts": pick("episode", limit=3, size=500),
                "note": "" if items else "Nothing was recorded for that meeting."}

    return ToolDef(
        "meeting_lookup", {}, handler, optional={"which": str},
        description="Get what was recorded in one earlier meeting of this section: pass which='last' for the previous "
                    "meeting, 'first', or a meeting number such as '2'. Say a short \"Hmm, let me check\" first, then "
                    "answer from the result with its date and time, or say plainly that nothing is recorded.",
        timeout_s=timeout_s, permission="memory_read", side_effect=False)


def _resolve_meeting(sessions: list[MeetingSession], current_session_id: str, which: str) -> MeetingSession | None:
    ordered = sorted(sessions, key=lambda m: m.number)
    earlier = [m for m in ordered if m.session_id != current_session_id]
    w = which.strip().lower()
    if w in ("", "last", "previous", "latest", "most recent"):
        return earlier[-1] if earlier else None
    if w == "first":
        return ordered[0] if ordered else None
    digits = "".join(c for c in w if c.isdigit())
    return next((m for m in ordered if digits and m.number == int(digits)), None)


def delete_meeting_tool(service: MemoryService, scope: MemoryScope, gate: MemoryWriteGate, current_session_id: str, *,
                        clock: Callable[[], float] = time.monotonic, ask_window_s: float = 180.0, timeout_s: float = 8.0) -> ToolDef:
    """Delete one earlier meeting. Unimportant meetings go straight away; important ones need a spoken yes first.

    The model cannot skip the question: confirm=true is accepted only after this tool asked, and only if the
    participant's own words since then contain a clear yes. A deleted meeting is hidden, not erased from the table.
    """
    asked: dict[int, float] = {}

    async def handler(which: str = "last", confirm: bool = False) -> dict[str, object]:
        if not gate.delete_meeting_allowed():
            raise PermissionDenied("delete_meeting needs the participant to ask for it, and I did not hear that request")
        target = _resolve_meeting(await service.sessions(scope), current_session_id, which)
        if target is None:
            return {"status": "not_found", "say": "Tell the participant plainly that there is no such meeting."}
        if target.session_id == current_session_id:
            return {"status": "refused", "say": "Tell the participant this meeting is still running, so it cannot be deleted now."}
        important, reasons, summary = await service.assess_meeting(scope, target.session_id)
        label = f"meeting {target.number} ({span(target)})"
        if important:
            if not confirm:
                asked[target.number] = clock()
                return {"status": "confirmation_required", "meeting": target.number, "when": span(target), "why_important": reasons,
                        "summary": summary[:400],
                        "say": f"Tell the participant that {label} looks important ({'; '.join(reasons)}), say briefly what it "
                               "covered, and ask: are you sure you want to delete it? Do not delete yet. Call this tool again with "
                               "confirm=true only after they clearly say yes."}
            t0 = asked.get(target.number)
            if t0 is None or clock() - t0 > ask_window_s:
                return {"status": "confirmation_required", "say": "I have not asked yet, or the question expired. Ask the participant "
                        "whether they are sure, then call again with confirm=true after a clear yes."}
            if not gate.confirmed_since(t0):
                raise ToolError("not_confirmed", "no clear yes heard yet", retryable=True,
                                details={"reason": "I have not heard a clear yes from the participant since I asked"})
        deleted = await service.delete_meeting(scope, target.session_id)
        asked.pop(target.number, None)
        return {"status": "deleted", "meeting": target.number, "records_removed": deleted,
                "say": f"Tell the participant {label} is deleted. Never claim this unless status is deleted."}

    return ToolDef(
        "delete_meeting", {}, handler, optional={"which": str, "confirm": bool},
        description="Delete an earlier meeting of this section on the participant's request: which='last', 'first' or a "
                    "number. If it returns confirmation_required, ask the participant 'are you sure?' and wait for a clear "
                    "yes before calling again with confirm=true. Report success only when status is deleted.",
        timeout_s=timeout_s, permission="memory_write", side_effect=True, live_write=True)


def update_memory_tool(service: MemoryService, scope: MemoryScope, gate: MemoryWriteGate, *, source: str, session_id: str,
                       timeout_s: float = 6.0) -> ToolDef:
    """Replace a stored memory with a corrected version. The old one is kept as superseded history."""
    async def handler(text: str, memory_id: str = "", match: str = "") -> dict[str, object]:
        if not gate.update_allowed():
            raise PermissionDenied("update_memory needs the participant to ask for the change, and I did not hear that request")
        new_text = text.strip()[:MAX_MEMORY_CHARS]
        if not new_text:
            raise ToolError("bad_args", "no new text", details={"reason": "I need the corrected wording"})
        prev: MemoryItem | None = None
        if memory_id.strip():
            prev = await service.store.get(scope, memory_id.strip())
        elif match.strip():
            hits = await service.recall(scope, match.strip(), limit=5)
            hits = [h for h in hits if h.item.kind not in ("episode", "summary")]
            if len(hits) > 1:
                return {"status": "ambiguous", "options": [{"id": h.item.id, "text": h.item.text[:200]} for h in hits[:4]],
                        "say": "Tell the participant there are several matches, read them briefly, and ask which one to change."}
            prev = hits[0].item if hits else None
        else:
            raise ToolError("bad_args", "need memory_id or match", details={"reason": "say which memory to change"})
        if prev is None or prev.meeting_id not in (None, scope.meeting_id):
            return {"status": "not_found", "say": "Tell the participant plainly that you could not find that memory."}
        if prev.kind in ("episode", "summary"):
            return {"status": "refused", "say": "Tell the participant transcripts and summaries cannot be edited, but you can "
                                                 "save a correction as a new memory."}
        item = await service.remember(scope, new_text, source=source, kind=prev.kind, session_id=session_id,
                                      supersedes_id=prev.id, metadata={"updated_from": prev.id})
        return {"status": "updated", "id": item.id, "was": prev.text[:200],
                "say": "Tell the participant in one short sentence what it said before and what it says now."}

    return ToolDef(
        "update_memory", {"text": str}, handler, optional={"memory_id": str, "match": str},
        description="Change a stored memory when the participant says it is wrong or asks to update it. Pass the corrected "
                    "full wording as text, and either memory_id (from recall_memory) or match (words from the old memory). "
                    "Report success only when status is updated; if ambiguous, ask which one.",
        timeout_s=timeout_s, permission="memory_write", side_effect=True, live_write=True)


def memory_tool_grants(service: MemoryService, scope: MemoryScope, gate: MemoryWriteGate,
                       session_id: str) -> tuple[tuple[ToolDef, ToolSpec], ...]:
    """Every memory tool for one live session, each with its grant. The scope and gate are bound here, never by the model."""
    def grant(name: str) -> ToolSpec:
        return ToolSpec(name, "memory", requires_human_approval=False)
    return (
        (remember_memory_tool(service, scope, gate, source=session_id, session_id=session_id), grant("remember_memory")),
        (recall_memory_tool(service, scope), grant("recall_memory")),
        (forget_memory_tool(service, scope, gate), grant("forget_memory")),
        (update_memory_tool(service, scope, gate, source=session_id, session_id=session_id), grant("update_memory")),
        (delete_meeting_tool(service, scope, gate, session_id), grant("delete_meeting")),
        (list_meetings_tool(service, scope, session_id), grant("list_meetings")),
        (meeting_lookup_tool(service, scope, session_id), grant("meeting_lookup")),
    )
