"""Open items that carry over from meeting to meeting: action items (with owner and deadline) and parked topics.

They are ordinary memory records (kind "action", or kind "note" with metadata type "parked") whose metadata says whether they
are open or done, so they follow the same section scoping, retention and soft delete as everything else. The model adds and
completes them through tools; the application checks that the participant actually asked.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..errors import PermissionDenied, ToolError
from ..memory.intent import MemoryWriteGate
from ..memory.models import MAX_MEMORY_CHARS, MemoryItem, MemoryScope, utcnow
from ..memory.service import MemoryService, when
from ..tools import ToolDef

OPEN, DONE = "open", "done"


def is_tracked(item: MemoryItem) -> bool:
    return item.kind == "action" or (item.kind == "note" and item.metadata.get("type") == "parked")


def item_type(item: MemoryItem) -> str:
    return "parked" if item.kind == "note" else "action"


def is_open(item: MemoryItem) -> bool:
    return is_tracked(item) and item.metadata.get("state", OPEN) != DONE


class OpenItems:
    def __init__(self, service: MemoryService, scope: MemoryScope, session_id: str):
        self.service, self.scope, self.session_id = service, scope, session_id

    async def add(self, kind: str, text: str, owner: str = "", deadline: str = "") -> MemoryItem:
        body = " ".join(text.split())[:MAX_MEMORY_CHARS]
        if not body:
            raise ToolError("bad_args", "empty", details={"reason": "tell me what the item is"})
        meta: dict[str, Any] = {"type": kind, "state": OPEN, "auto": False}
        if owner.strip():
            meta["owner"] = owner.strip()[:80]
        if deadline.strip():
            meta["deadline"] = deadline.strip()[:80]
        return await self.service.remember(
            self.scope, body, source=f"session:{self.session_id}:{kind}", kind="action" if kind == "action" else "note",
            session_id=self.session_id, metadata=meta)

    async def open_items(self, kind: str = "all") -> list[MemoryItem]:
        items = await self.service.store.list_recent(self.scope, 400)
        return [i for i in items if is_open(i) and kind in ("all", item_type(i))]

    async def describe(self, items: list[MemoryItem]) -> list[dict[str, str]]:
        known = {m.session_id: m for m in await self.service.sessions(self.scope)}
        out = []
        for i in items:
            row = {"id": i.id, "type": item_type(i), "text": i.text[:300]}
            for k in ("owner", "deadline"):
                if i.metadata.get(k):
                    row[k] = str(i.metadata[k])
            if i.status == "candidate":
                row["note"] = "unconfirmed: picked up automatically"
            m = known.get(i.session_id)
            if m is not None:
                row["from_meeting"] = str(m.number)
                row["raised"] = when(m.started_at)
            out.append(row)
        return out

    async def complete(self, item: MemoryItem) -> MemoryItem:
        done = replace(item, updated_at=utcnow(), status="confirmed", expires_at=None,
                       metadata={**item.metadata, "state": DONE, "done_at": utcnow().isoformat(), "done_in": self.session_id})
        await self.service.store.update(done)
        return done


def add_action_tool(t: OpenItems, gate: MemoryWriteGate) -> ToolDef:
    async def handler(text: str, owner: str = "", deadline: str = "") -> dict[str, object]:
        if not gate.action_allowed():
            raise PermissionDenied("add_action needs the participant to ask for it, and I did not hear that request")
        item = await t.add("action", text, owner, deadline)
        return {"status": "added", "id": item.id, "owner": owner, "deadline": deadline,
                "say": "Confirm in one short sentence what was added, with owner and deadline if given."}

    return ToolDef("add_action", {"text": str}, handler, optional={"owner": str, "deadline": str},
                   description="Add an action item when the participant asks you to. text is the task; owner and deadline "
                               "exactly as they said them. It carries over to later meetings until completed.",
                   timeout_s=5.0, permission="memory_write", side_effect=True, live_write=True)


def park_item_tool(t: OpenItems, gate: MemoryWriteGate) -> ToolDef:
    async def handler(text: str) -> dict[str, object]:
        if not gate.park_allowed():
            raise PermissionDenied("park_item needs the participant to ask for it, and I did not hear that request")
        item = await t.add("parked", text)
        return {"status": "parked", "id": item.id,
                "say": "Say in a few words that it is in the parking lot and will come up again next meeting."}

    return ToolDef("park_item", {"text": str}, handler,
                   description="Put an off-topic point in the parking lot when the participant asks. It is raised again at the "
                               "start of the next meeting until someone resolves it.",
                   timeout_s=5.0, permission="memory_write", side_effect=True, live_write=True)


def list_open_items_tool(t: OpenItems) -> ToolDef:
    async def handler(type: str = "all") -> dict[str, object]:
        kind = type.strip().lower()
        kind = {"actions": "action", "tasks": "action", "parking": "parked", "parked": "parked", "parking lot": "parked"}.get(kind, kind)
        if kind not in ("all", "action", "parked"):
            kind = "all"
        rows = await t.describe(await t.open_items(kind))
        return {"count": len(rows), "items": rows[:15],
                "note": "" if rows else "Nothing is open. Tell the participant plainly that nothing is open."}

    return ToolDef("list_open_items", {}, handler, optional={"type": str},
                   description="List what is still open across the meetings of this section: action items (with owner and "
                               "deadline) and parked topics. type is all, action or parked. Say a short \"Hmm, let me check\" "
                               "first, and say how many there are before reading them.",
                   timeout_s=6.0, permission="memory_read", side_effect=False)


def complete_item_tool(t: OpenItems, gate: MemoryWriteGate) -> ToolDef:
    async def handler(match: str = "", item_id: str = "") -> dict[str, object]:
        if not gate.complete_allowed():
            raise PermissionDenied("complete_item needs the participant to say it is done, and I did not hear that")
        open_now = await t.open_items()
        if item_id.strip():
            pool = [i for i in open_now if i.id == item_id.strip()]
        elif match.strip():
            from ..memory.models import tokens
            want = tokens(match)
            scored = [(len(want & tokens(i.text)), i) for i in open_now]
            best = max((s for s, _ in scored), default=0)
            pool = [i for s, i in scored if s == best and s > 0]
        else:
            raise ToolError("bad_args", "need match or item_id", details={"reason": "say which item is done"})
        if not pool:
            return {"status": "not_found", "say": "Tell the participant plainly that no open item matches."}
        if len(pool) > 1:
            return {"status": "ambiguous", "options": [{"id": i.id, "text": i.text[:160]} for i in pool[:4]],
                    "say": "Read the matches briefly and ask which one is done."}
        done = await t.complete(pool[0])
        left = len(await t.open_items()) 
        return {"status": "done", "id": done.id, "text": done.text[:200], "still_open": left,
                "say": "Confirm which item is marked done and how many are still open."}

    return ToolDef("complete_item", {}, handler, optional={"match": str, "item_id": str},
                   description="Mark an open action item or parked topic as done when the participant says it is finished. Pass "
                               "words from the item as match, or its id from list_open_items. Report success only when status is done.",
                   timeout_s=6.0, permission="memory_write", side_effect=True, live_write=True)
