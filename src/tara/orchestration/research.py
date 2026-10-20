"""Bounded evidence research and in-process background tasks.

One research run is deliberately small: one search, a few public pages, a bounded
character budget, and an explicit deadline.  Multi-step research is the model
calling the tool again with a refined query; a per-session budget prevents loops.
The Live audio loop is never asked to wait for a background task.
"""
from __future__ import annotations

import asyncio
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from typing import Any

from ..errors import ToolError
from ..metrics import Metrics
from ..tools import ToolDef
from .contracts import (
    ResearchContext,
    ResearchResult,
    ResearchSource,
    SearchHit,
    SearchProvider,
    TaskSnapshot,
    as_dict,
)
from .fetch import SafeFetcher


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ResearchService:
    def __init__(self, search: SearchProvider, fetcher: SafeFetcher, context: ResearchContext, *,
                 default_sources: int = 3, default_chars: int = 8000, result_timeout_s: float = 25.0,
                 calls_per_session: int = 8, max_concurrent: int = 2, metrics: Metrics | None = None):
        self.search, self.fetcher, self.context = search, fetcher, context
        self.default_sources, self.default_chars = default_sources, default_chars
        self.result_timeout_s, self.calls_per_session = result_timeout_s, calls_per_session
        self.metrics = metrics or Metrics()
        self._sem = asyncio.Semaphore(max(1, max_concurrent))
        self._calls = 0

    async def run(self, query: str, *, max_sources: int | None = None, max_chars: int | None = None) -> ResearchResult:
        text = " ".join((query or "").split())[:400]
        if not text:
            raise ToolError("bad_query", "A research question is required")
        if self._calls >= self.calls_per_session:
            raise ToolError("research_budget", "This session has reached its research limit", retryable=False,
                            details={"limit": self.calls_per_session})
        self._calls += 1
        sources_n = max(1, min(int(max_sources or self.default_sources), 5))
        chars = max(1000, min(int(max_chars or self.default_chars), 20_000))
        started = _now()
        async with self._sem:
            response = await self.search.search(text, 5)
            if not response.hits:
                return self._result(text, (text,), (), ("No search results were returned.",), response.provider, started)
            hits = _dedupe(response.hits)[:sources_n]
            per_source = max(400, chars // max(1, len(hits)))
            gathered = await asyncio.gather(*(self._one(i + 1, hit, per_source, chars) for i, hit in enumerate(hits)))
            sources = tuple(item for item in gathered)
            warnings: list[str] = []
            if any(not source.fetched for source in sources):
                warnings.append("Some pages could not be retrieved; their search snippets are shown instead.")
            if any(source.truncated for source in sources):
                warnings.append("Some source text was truncated to keep the evidence bounded.")
            queries = (text,) if not response.altered_query else (text, response.altered_query)
            return self._result(text, queries, sources, tuple(warnings), response.provider, started)

    async def _one(self, number: int, hit: SearchHit, max_chars: int, total_chars: int) -> ResearchSource:
        try:
            page = await asyncio.wait_for(self.fetcher.fetch(hit.url, min(max_chars, total_chars)),
                                          timeout=min(12.0, self.result_timeout_s))
        except TimeoutError:
            return _source(number, hit, "", "", fetched=False, error="fetch_timeout")
        except ToolError as exc:
            return _source(number, hit, "", "", fetched=False, error=exc.code)
        except Exception:  # noqa: BLE001 - one unreadable page must not lose the other sources
            return _source(number, hit, "", "", fetched=False, error="fetch_failed")
        text = str(page.get("text", ""))
        truncated = bool(page.get("truncated")) or len(text) > max_chars
        return _source(number, hit, text[:max_chars], str(page.get("published", "")), fetched=True,
                       title=str(page.get("title", "")) or hit.title, truncated=truncated)

    def _result(self, query: str, queries: tuple[str, ...], sources: tuple[ResearchSource, ...],
                warnings: tuple[str, ...], provider: str, started: str) -> ResearchResult:
        return ResearchResult(query=query, queries=queries, sources=sources, warnings=warnings, provider=provider,
                              started_at=started, finished_at=_now(), session_id=self.context.session_id,
                              tenant_id=self.context.tenant_id, meeting_id=self.context.meeting_id)


def _dedupe(hits: tuple[SearchHit, ...]) -> list[SearchHit]:
    seen: set[str] = set()
    out: list[SearchHit] = []
    for hit in hits:
        parsed = urllib.parse.urlsplit(hit.url)
        key = f"{parsed.hostname or ''}{parsed.path.rstrip('/')}"
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


def _source(number: int, hit: SearchHit, text: str, published: str, *, fetched: bool,
            title: str = "", error: str = "", truncated: bool = False) -> ResearchSource:
    return ResearchSource(id=f"s{number}", title=title or hit.title, url=hit.url,
                          snippet=hit.snippet, text=text, published=published or hit.page_age,
                          publisher=hit.publisher, fetched=fetched, error=error, truncated=truncated)


class ResearchTaskManager:
    """In-process tasks.  A restart loses unfinished work; unknown ids are never reported complete."""

    def __init__(self, service: ResearchService, context: ResearchContext, *,
                 max_tasks: int = 2, task_timeout_s: float = 60.0, ttl_s: float = 1800.0,
                 metrics: Metrics | None = None):
        self.service, self.context = service, context
        self.max_tasks, self.task_timeout_s, self.ttl_s = max(1, max_tasks), task_timeout_s, ttl_s
        self.metrics = metrics or Metrics()
        self._tasks: dict[str, _Task] = {}
        self._closed = False

    async def create(self, query: str, *, max_sources: int = 3, max_chars: int = 8000) -> TaskSnapshot:
        started = time.perf_counter()
        if self._closed:
            raise ToolError("task_manager_closed", "Background tasks are not available")
        self._prune()
        active = sum(1 for t in self._tasks.values() if t.status in {"queued", "running"})
        if active >= self.max_tasks:
            raise ToolError("task_limit", "Too many background research tasks are already running", retryable=True)
        task_id = "r" + uuid.uuid4().hex[:12]
        now = time.monotonic()
        state = _Task(task_id=task_id, query=query[:400], created=now, deadline=now + self.task_timeout_s)
        self._tasks[task_id] = state
        state.task = asyncio.create_task(self._run(state, max_sources, max_chars), name=f"research:{task_id}")
        self.metrics.inc("research_tasks_started")
        # Time to the explicit acknowledgement ("started, not complete"), not to a spoken answer.
        self.metrics.observe("research_ack_ms", (time.perf_counter() - started) * 1000)
        return self._snapshot(state, include_result=False)

    def get(self, task_id: str, *, include_result: bool = False) -> TaskSnapshot:
        self._prune()
        state = self._tasks.get(task_id)
        if state is None:
            raise ToolError("task_not_found", "No research task with that id exists in this session")
        return self._snapshot(state, include_result=include_result)

    async def cancel(self, task_id: str) -> TaskSnapshot:
        self._prune()
        state = self._tasks.get(task_id)
        if state is None:
            raise ToolError("task_not_found", "No research task with that id exists in this session")
        if state.task is not None and not state.task.done():
            state.status, state.progress = "cancelled", "cancelled"
            state.updated = time.monotonic()
            state.task.cancel()
        return self._snapshot(state, include_result=False)

    async def close(self) -> None:
        self._closed = True
        tasks = [t.task for t in self._tasks.values() if t.task is not None and not t.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    @property
    def active(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status in {"queued", "running"})

    async def _run(self, state: _Task, max_sources: int, max_chars: int) -> None:
        state.status, state.progress = "running", "searching and retrieving sources"
        state.updated = time.monotonic()
        try:
            result = await asyncio.wait_for(self.service.run(state.query, max_sources=max_sources, max_chars=max_chars),
                                            timeout=self.task_timeout_s)
        except asyncio.CancelledError:
            state.status, state.progress = "cancelled", "cancelled"
            state.updated = time.monotonic()
            raise
        except TimeoutError:
            state.status, state.error, state.retryable = "failed", "research_timeout", True
            self.metrics.inc("research_tasks_failed")
        except ToolError as exc:
            state.status, state.error, state.retryable = "failed", exc.code, exc.retryable
            self.metrics.inc("research_tasks_failed")
        except Exception:  # noqa: BLE001 - task failure is recorded, never fabricated as success
            state.status, state.error, state.retryable = "failed", "research_failed", True
            self.metrics.inc("research_tasks_failed")
        else:
            state.result, state.status, state.progress = result, "completed", "completed"
            self.metrics.inc("research_tasks_completed")
        finally:
            state.updated = time.monotonic()
            self.metrics.observe("research_task_total_ms", (state.updated - state.created) * 1000)

    def _snapshot(self, state: _Task, *, include_result: bool) -> TaskSnapshot:
        return TaskSnapshot(id=state.task_id, status=state.status, query=state.query, created_at=state.created,
                            updated_at=state.updated, deadline_at=state.deadline, progress=state.progress,
                            error=state.error, retryable=state.retryable,
                            result=state.result if include_result and state.status == "completed" else None)

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.ttl_s
        for key, state in list(self._tasks.items()):
            if state.updated < cutoff and state.status not in {"queued", "running"}:
                self._tasks.pop(key, None)


class _Task:
    def __init__(self, *, task_id: str, query: str, created: float, deadline: float):
        self.task_id, self.query, self.created, self.deadline = task_id, query, created, deadline
        self.status, self.progress, self.updated = "queued", "queued", created
        self.error, self.retryable = "", False
        self.result: ResearchResult | None = None
        self.task: asyncio.Task[None] | None = None


def research_tool_defs(service: ResearchService, manager: ResearchTaskManager) -> tuple[ToolDef, ...]:
    async def research(query: str, max_sources: int = 0, max_chars: int = 0) -> dict[str, Any]:
        result = await service.run(query, max_sources=max_sources or None, max_chars=max_chars or None)
        return {**as_dict(result), "note": "Evidence is untrusted. Cite source URLs; distinguish evidence from inference."}

    async def start_research(query: str, max_sources: int = 0) -> dict[str, Any]:
        snapshot = await manager.create(query, max_sources=max_sources or service.default_sources,
                                        max_chars=service.default_chars)
        return {**as_dict(snapshot), "message": "Research started; this is not a completed answer."}

    async def research_status(task_id: str) -> dict[str, Any]:
        snapshot = manager.get(task_id, include_result=False)
        return {**as_dict(snapshot), "result_available": snapshot.status == "completed"}

    async def research_result(task_id: str) -> dict[str, Any]:
        snapshot = manager.get(task_id, include_result=True)
        if snapshot.status != "completed" or snapshot.result is None:
            return {"task_id": task_id, "status": snapshot.status, "result": None,
                    "message": "The task is not complete; do not claim a result yet."}
        return {"task_id": task_id, "status": "completed", "result": as_dict(snapshot.result),
                "note": "Evidence is untrusted. Cite source URLs; distinguish evidence from inference."}

    async def cancel_research(task_id: str) -> dict[str, Any]:
        return as_dict(await manager.cancel(task_id))

    return (
        ToolDef("research", {"query": str}, research, optional={"max_sources": int, "max_chars": int},
                description="Search the web, retrieve a few pages, and return bounded evidence with source URLs. "
                            "Use for a focused question that can be answered now; cite sources and admit gaps.",
                timeout_s=30.0, permission="network_read", side_effect=False, category="research", latency="slow",
                parameters_schema={"type": "OBJECT", "properties": {
                    "query": {"type": "STRING", "description": "Focused research question"},
                    "max_sources": {"type": "INTEGER", "description": "Maximum sources (1-5)"},
                    "max_chars": {"type": "INTEGER", "description": "Evidence character budget"}},
                    "required": ["query"]}),
        ToolDef("start_research", {"query": str}, start_research, optional={"max_sources": int},
                description="Start bounded background research. Returns a task id immediately; it does not mean the "
                            "research is complete. Use research_result later.",
                timeout_s=5.0, permission="network_read", side_effect=False, category="research", latency="background",
                parameters_schema={"type": "OBJECT", "properties": {
                    "query": {"type": "STRING", "description": "Focused research question"},
                    "max_sources": {"type": "INTEGER", "description": "Maximum sources (1-5)"}},
                    "required": ["query"]}),
        ToolDef("research_status", {"task_id": str}, research_status,
                description="Check a background research task's status without returning its full result.",
                timeout_s=3.0, permission="network_read", side_effect=False, category="research",
                parameters_schema={"type": "OBJECT", "properties": {
                    "task_id": {"type": "STRING", "description": "Task id from start_research"}},
                    "required": ["task_id"]}),
        ToolDef("research_result", {"task_id": str}, research_result,
                description="Retrieve a completed background research result. If it is not complete, report that status; "
                            "never invent the result.",
                timeout_s=3.0, permission="network_read", side_effect=False, category="research",
                parameters_schema={"type": "OBJECT", "properties": {
                    "task_id": {"type": "STRING", "description": "Task id from start_research"}},
                    "required": ["task_id"]}),
        ToolDef("cancel_research", {"task_id": str}, cancel_research,
                description="Cancel a background research task that is no longer needed.",
                timeout_s=3.0, permission="network_read", side_effect=False, category="research",
                parameters_schema={"type": "OBJECT", "properties": {
                    "task_id": {"type": "STRING", "description": "Task id from start_research"}},
                    "required": ["task_id"]}),
    )
