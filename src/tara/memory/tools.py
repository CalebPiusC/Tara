"""Tool definitions for explicit long-term memory.

Writes are code-authorized: the handler checks `MemoryWriteGate`, which only opens
after the participant's final transcript contains an explicit request. The model
cannot choose the tenant, user, session or source.
"""
from __future__ import annotations

from ..errors import PermissionDenied
from ..tools import ToolDef
from .intent import MemoryWriteGate
from .models import MemoryScope
from .service import MemoryService


def remember_memory_tool(service: MemoryService, scope: MemoryScope, gate: MemoryWriteGate, *,
                         source: str, session_id: str, timeout_s: float = 5.0) -> ToolDef:
    async def handler(text: str, kind: str = "fact") -> dict[str, str]:
        if not gate.remember_allowed():
            raise PermissionDenied("remember_memory requires an explicit request from the participant")
        item = await service.remember(scope, text, source=source, kind=kind, session_id=session_id)
        return {"status": "saved", "id": item.id, "kind": item.kind}

    return ToolDef(
        "remember_memory", {"text": str}, handler, optional={"kind": str},
        description="Save a fact, preference, project detail or decision the participant explicitly asked you to "
                    "remember. Never call this for ordinary conversation. Confirm the save only after this tool "
                    "returns status=saved; if it returns an error, say the save failed.",
        timeout_s=timeout_s, permission="memory_write", side_effect=True, live_write=True)


def recall_memory_tool(service: MemoryService, scope: MemoryScope, *, timeout_s: float = 5.0) -> ToolDef:
    async def handler(query: str, limit: int = 5) -> list[dict[str, str]]:
        hits = await service.recall(scope, query, limit=max(1, min(limit, 10)))
        return [{"id": h.item.id, "text": h.item.text, "kind": h.item.kind, "source": h.item.source,
                 "updated_at": h.item.updated_at.isoformat()} for h in hits]

    return ToolDef(
        "recall_memory", {"query": str}, handler, optional={"limit": int},
        description="Search long-term memory from earlier conversations. Use this whenever the participant asks "
                    "what you remember or refers to something discussed before. If no result is returned, say you "
                    "do not have that memory; never invent it.",
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
