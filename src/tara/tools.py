"""Permission-gated, typed, time-limited tool execution.

The model may *propose* a ToolCall. Nothing runs unless the tool is registered in code
AND granted by the validated MeetingConfig (requested ∩ app policy). Arguments are
type-checked, runtime is bounded by a timeout, and every call is audited (ids and
outcomes, never argument values). Tool output is untrusted data.

Side-effecting tools run only AFTER the runtime approves a response. Read-only tools
(`side_effect=False`) may run during generation through `execute_read_only`.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import ToolSpec
from .contracts import Passage, ToolCall, ToolResult
from .errors import PermissionDenied, ProviderError, ToolError
from .metrics import Metrics


@dataclass(frozen=True)
class ToolDef:
    name: str
    required: dict[str, type]
    handler: Callable[..., Any]
    optional: dict[str, type] = field(default_factory=dict)
    description: str = ""
    timeout_s: float = 10.0
    permission: str = ""           # informational label; the grant itself is the MeetingConfig tool list
    side_effect: bool = True       # conservative default
    live_write: bool = False       # code-owned exception: handler enforces its own explicit authorization
    category: str = "general"
    cost: str = "free"             # "free" | "metered" | "model"
    latency: str = "fast"          # "fast" | "slow" | "background"
    parameters_schema: dict[str, Any] | None = None   # explicit JSON schema when types alone are not enough


def _json_type(t: type) -> str:
    return {str: "STRING", int: "INTEGER", float: "NUMBER", bool: "BOOLEAN"}.get(t, "STRING")


class ToolExecutor:
    def __init__(self, registry: dict[str, ToolDef], grants: tuple[ToolSpec, ...], metrics: Metrics | None = None):
        for n, d in registry.items():
            if n != d.name:
                raise ValueError(f"registry key {n!r} != tool name {d.name!r}")
        self._reg = registry
        self._grants = {g.name: g for g in grants}
        self.metrics = metrics or Metrics()
        self.audit: list[dict[str, Any]] = []
        self._approved: set[str] = set()

    def approve(self, call_id: str) -> None:
        """Human approval for a call that returned pending_approval."""
        self._approved.add(call_id)

    def register_and_grant(self, definition: ToolDef, grant: ToolSpec) -> None:
        """Add a code-owned tool at composition time (the application, never the model, owns both sides)."""
        if definition.name != grant.name:
            raise ValueError(f"tool name {definition.name!r} != grant name {grant.name!r}")
        if definition.name in self._reg or definition.name in self._grants:
            raise ValueError(f"tool {definition.name!r} is already registered or granted")
        self._reg[definition.name] = definition
        self._grants[definition.name] = grant

    def describe(self) -> list[dict[str, Any]]:
        """What the model may be told about: granted AND registered tools only."""
        out = []
        for name in self._grants:
            d = self._reg.get(name)
            if d:
                out.append({"name": name, "description": d.description, "read_only": not d.side_effect,
                            "params": {k: t.__name__ for k, t in {**d.required, **d.optional}.items()},
                            "required": sorted(d.required)})
        return out

    def allows_live_write(self, name: str) -> bool:
        """True only for a code-owned tool whose handler checks explicit authorization."""
        d = self._reg.get(name)
        return d is not None and d.live_write

    def catalog(self) -> list[dict[str, Any]]:
        """Model-safe declarations plus operational metadata for telemetry and tests.

        `describe` remains the stable minimal surface used by older callers; `catalog`
        adds schemas, async/side-effect flags and cost/latency labels.  Neither method
        reveals argument values or secrets.
        """
        out = []
        for name in self._grants:
            d = self._reg.get(name)
            if d:
                schema = d.parameters_schema or {
                    "type": "OBJECT",
                    "properties": {k: {"type": _json_type(t)} for k, t in {**d.required, **d.optional}.items()},
                    "required": sorted(d.required),
                }
                out.append({"name": name, "description": d.description, "read_only": not d.side_effect,
                            "side_effect": d.side_effect, "async": inspect.iscoroutinefunction(d.handler),
                            "params": {k: t.__name__ for k, t in {**d.required, **d.optional}.items()},
                            "required": sorted(d.required), "schema": schema, "timeout_s": d.timeout_s,
                            "category": d.category, "cost": d.cost, "latency": d.latency})
        return out

    async def execute_read_only(self, call: ToolCall, call_id: str) -> ToolResult:
        d = self._reg.get(call.name)
        if d is not None and d.side_effect:
            return self._deny(call, call_id, "side_effect_requires_approval")
        return await self.execute(call, call_id)

    async def execute(self, call: ToolCall, call_id: str) -> ToolResult:
        grant = self._grants.get(call.name)
        if grant is None:
            return self._deny(call, call_id, "not_granted")
        d = self._reg.get(call.name)
        if d is None:
            return self._deny(call, call_id, "not_registered")
        err = self._check_args(d, call.args)
        if err:
            return self._deny(call, call_id, f"bad_args:{err}")
        if grant.requires_human_approval and call_id not in self._approved:
            return self._deny(call, call_id, "pending_approval", pending=True)
        t0 = time.perf_counter()
        try:
            if inspect.iscoroutinefunction(d.handler):
                res = await asyncio.wait_for(d.handler(**call.args), d.timeout_s)
            else:
                res = await asyncio.wait_for(asyncio.to_thread(d.handler, **call.args), d.timeout_s)
                if inspect.isawaitable(res):
                    res = await asyncio.wait_for(res, d.timeout_s)
        except TimeoutError:
            return self._finish(call, call_id, t0, "timeout", error="tool_timeout", retryable=True)
        except asyncio.CancelledError:
            raise
        except ToolError as e:
            return self._finish(call, call_id, t0, f"error:{e.code}", error=e.code,
                                details=e.details, retryable=e.retryable)
        except ProviderError as e:
            # Provider messages may contain request details; only the class is exposed.
            return self._finish(call, call_id, t0, f"error:{type(e).__name__}",
                                error=f"tool_error:{type(e).__name__}", retryable=True)
        except PermissionDenied as e:     # our own fixed message, safe to show the model
            return self._finish(call, call_id, t0, "error:PermissionDenied", error="tool_error:PermissionDenied",
                                details={"reason": str(e)[:200]})
        except Exception as e:  # noqa: BLE001 - tool failure is reported, not hidden, never retried
            return self._finish(call, call_id, t0, f"error:{type(e).__name__}", error=f"tool_error:{type(e).__name__}")
        self._finish(call, call_id, t0, "executed")
        return ToolResult(True, output=res)

    def _finish(self, call: ToolCall, call_id: str, t0: float, outcome: str, error: str | None = None,
                details: dict[str, Any] | None = None, retryable: bool = False) -> ToolResult:
        ms = (time.perf_counter() - t0) * 1000
        self.audit.append({"call_id": call_id, "tool": call.name, "outcome": outcome, "ms": round(ms, 1),
                           "retryable": retryable})
        self.metrics.observe(f"tool_ms.{call.name}", ms)
        if error:
            self.metrics.inc("tool_failures")
        return ToolResult(False, error=error, retryable=retryable, details=dict(details or {}))

    def _deny(self, call: ToolCall, call_id: str, why: str, pending: bool = False) -> ToolResult:
        self.audit.append({"call_id": call_id, "tool": call.name, "outcome": why})
        self.metrics.inc("tool_denied")
        return ToolResult(False, error=why, pending_approval=pending)

    @staticmethod
    def _check_args(d: ToolDef, args: dict[str, Any]) -> str | None:
        allowed = {**d.required, **d.optional}
        for k in d.required:
            if k not in args:
                return f"missing:{k}"
        for k, v in args.items():
            if k not in allowed:
                return f"unknown:{k}"
            expected = allowed[k]
            if expected is float and isinstance(v, int) and not isinstance(v, bool):
                continue
            if not isinstance(v, expected) or (isinstance(v, bool) and expected is not bool):
                return f"type:{k}"
        return None


# ---- web search interface (no implementation ships; never invent results) ----
class WebSearch(Protocol):
    async def search(self, query: str, k: int = 3) -> list[Passage]: ...


class NotConfiguredSearch:
    async def search(self, query: str, k: int = 3) -> list[Passage]:
        raise ProviderError("web search is not configured")


def web_search_tool(search: WebSearch, timeout_s: float = 10.0) -> ToolDef:
    async def handler(query: str) -> list[dict[str, str]]:
        return [{"id": p.id, "text": p.text, "source": p.source} for p in await search.search(query, 3)]
    return ToolDef("web_search", {"query": str}, handler, description="Search the web for current information.",
                   timeout_s=timeout_s, permission="web_search", side_effect=False)
