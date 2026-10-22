"""Bounded research, source provenance, and background task lifecycle."""
from __future__ import annotations

import asyncio

import pytest

from tara.core.errors import ToolError
from tara.tools.utility.contracts import HttpResponse, ResearchContext, SearchHit
from tara.tools.utility.fetch import SafeFetcher
from tara.tools.utility.research import ResearchService, ResearchTaskManager, research_tool_defs
from tests.tools.utility.fakes import PUBLIC_RESOLVER, FakeSearch, html_response


def context() -> ResearchContext:
    return ResearchContext("s1", "tenant", "meeting")


def fetcher(fail: set[str] | None = None) -> SafeFetcher:
    fail = fail or set()

    def transport(url: str, _timeout: float, _max: int) -> HttpResponse:
        if url in fail:
            raise ToolError("fetch_blocked", "blocked for the test")
        return html_response(f"<html><head><title>{url}</title></head><body>Evidence for {url}</body></html>", url)

    return SafeFetcher(resolver=PUBLIC_RESOLVER, transport=transport)


def test_research_returns_bounded_sources_with_provenance():
    async def go():
        service = ResearchService(FakeSearch(), fetcher(), context(), default_chars=1000)
        result = await service.run("current topic")
        assert result.session_id == "s1" and result.tenant_id == "tenant" and result.meeting_id == "meeting"
        assert result.provider == "fake" and len(result.sources) == 2
        assert result.sources[0].id == "s1" and result.sources[0].fetched is True
        assert "Evidence for" in result.sources[0].text

    asyncio.run(go())


def test_research_deduplicates_sources_and_records_partial_failures():
    hits = (SearchHit("A", "https://example.com/a", "s1"), SearchHit("A again", "https://example.com/a/", "s2"),
            SearchHit("B", "https://example.org/b", "s3"))
    async def go():
        service = ResearchService(FakeSearch(hits), fetcher(fail={"https://example.org/b"}), context())
        result = await service.run("q", max_sources=3)
        assert [source.url for source in result.sources] == ["https://example.com/a", "https://example.org/b"]
        assert result.sources[1].fetched is False and result.sources[1].error == "fetch_blocked"
        assert result.sources[1].text == ""
        assert result.warnings and "snippets" in result.warnings[0]

    asyncio.run(go())


def test_research_budget_is_enforced_and_errors_are_explicit():
    async def go():
        service = ResearchService(FakeSearch(), fetcher(), context(), calls_per_session=1)
        await service.run("one")
        with pytest.raises(ToolError) as exc:
            await service.run("two")
        assert exc.value.code == "research_budget"

    asyncio.run(go())


def test_background_task_completes_and_exposes_result_only_when_done():
    async def go():
        service = ResearchService(FakeSearch(delay=0.02), fetcher(), context())
        manager = ResearchTaskManager(service, context(), max_tasks=1, task_timeout_s=2)
        try:
            created = await manager.create("q", max_sources=2)
            assert created.status in {"queued", "running"}
            assert manager.get(created.id).result is None
            for _ in range(100):
                if manager.get(created.id).status == "completed":
                    break
                await asyncio.sleep(0.01)
            done = manager.get(created.id, include_result=True)
            assert done.status == "completed" and done.result is not None
            assert done.result.session_id == "s1"
            assert manager.metrics.summary("research_ack_ms")["count"] == 1
            assert manager.metrics.summary("research_task_total_ms")["count"] == 1
        finally:
            await manager.close()

    asyncio.run(go())


def test_background_task_can_be_cancelled_and_unknown_ids_are_not_complete():
    async def go():
        service = ResearchService(FakeSearch(delay=5), fetcher(), context())
        manager = ResearchTaskManager(service, context(), max_tasks=1, task_timeout_s=10)
        try:
            task = await manager.create("q")
            cancelled = await manager.cancel(task.id)
            assert cancelled.status == "cancelled"
            assert manager.get(task.id).status == "cancelled"
            with pytest.raises(ToolError) as exc:
                manager.get("rdoesnotexist")
            assert exc.value.code == "task_not_found"
        finally:
            await manager.close()

    asyncio.run(go())


def test_background_task_timeout_is_reported_as_failure():
    async def go():
        service = ResearchService(FakeSearch(delay=5), fetcher(), context())
        manager = ResearchTaskManager(service, context(), max_tasks=1, task_timeout_s=0.01)
        try:
            task = await manager.create("q")
            for _ in range(100):
                if manager.get(task.id).status == "failed":
                    break
                await asyncio.sleep(0.01)
            failed = manager.get(task.id)
            assert failed.status == "failed" and failed.error == "research_timeout"
            assert failed.result is None
        finally:
            await manager.close()

    asyncio.run(go())


def test_task_limit_and_tool_surface():
    async def go():
        service = ResearchService(FakeSearch(delay=1), fetcher(), context())
        manager = ResearchTaskManager(service, context(), max_tasks=1, task_timeout_s=10)
        try:
            first = await manager.create("q")
            with pytest.raises(ToolError) as exc:
                await manager.create("q2")
            assert exc.value.code == "task_limit"
            defs = {d.name: d for d in research_tool_defs(service, manager)}
            assert set(defs) == {"research", "start_research", "research_status", "research_result", "cancel_research"}
            status = await defs["research_status"].handler(first.id)
            assert status["status"] in {"queued", "running"}
            pending = await defs["research_result"].handler(first.id)
            assert pending["status"] in {"queued", "running"} and pending["result"] is None
        finally:
            await manager.close()

    asyncio.run(go())
