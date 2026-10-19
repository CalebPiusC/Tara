"""Permission-gated tool execution. The model proposes a ToolCall; this module
decides. Nothing runs unless the tool is registered in code AND granted by the
validated MeetingConfig (requested ∩ app policy). Args are schema-checked."""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import ToolSpec
from .contracts import ToolCall, ToolResult


@dataclass(frozen=True)
class ToolDef:
    name: str
    required: dict[str, type]
    handler: Callable[..., Any]
    optional: dict[str, type] = field(default_factory=dict)


class ToolExecutor:
    def __init__(self, registry: dict[str, ToolDef], grants: tuple[ToolSpec, ...]):
        self._reg = registry
        self._grants = {g.name: g for g in grants}
        self.audit: list[dict[str, Any]] = []
        self._approved: set[str] = set()

    def approve(self, call_id: str) -> None:
        """Human approval for a call that returned pending_approval."""
        self._approved.add(call_id)

    async def execute(self, call: ToolCall, call_id: str) -> ToolResult:
        def deny(why: str, pending: bool = False) -> ToolResult:
            self.audit.append({"call_id": call_id, "tool": call.name, "outcome": why})
            return ToolResult(False, error=why, pending_approval=pending)

        grant = self._grants.get(call.name)
        if grant is None:
            return deny("not_granted")
        d = self._reg.get(call.name)
        if d is None:
            return deny("not_registered")
        err = self._check_args(d, call.args)
        if err:
            return deny(f"bad_args:{err}")
        if grant.requires_human_approval and call_id not in self._approved:
            return deny("pending_approval", pending=True)
        try:
            res = d.handler(**call.args)
            if inspect.isawaitable(res):
                res = await res
        except Exception as e:  # tool failure is reported, not hidden; not retried
            self.audit.append({"call_id": call_id, "tool": call.name, "outcome": f"error:{type(e).__name__}"})
            return ToolResult(False, error=f"tool_error:{type(e).__name__}")
        self.audit.append({"call_id": call_id, "tool": call.name, "outcome": "executed"})
        return ToolResult(True, output=res)

    @staticmethod
    def _check_args(d: ToolDef, args: dict[str, Any]) -> str | None:
        allowed = {**d.required, **d.optional}
        for k, t in d.required.items():
            if k not in args:
                return f"missing:{k}"
        for k, v in args.items():
            if k not in allowed:
                return f"unknown:{k}"
            if not isinstance(v, allowed[k]) or isinstance(v, bool) and allowed[k] is not bool:
                return f"type:{k}"
        return None
