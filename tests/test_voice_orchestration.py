"""The Live engine executes orchestration tools through the existing gated path."""
from __future__ import annotations

import asyncio

from orchestration_fakes import PUBLIC_RESOLVER, FakeSearch, html_response
from test_voice_engine import mk, running, until

from tara.config import ToolSpec
from tara.orchestration import Orchestration
from tara.orchestration.contracts import HttpResponse
from tara.orchestration.fetch import SafeFetcher
from tara.settings import ToolSettings
from tara.tools import ToolExecutor
from tara.voice.engine import LiveParams
from tara.voice.events import ToolCallRequested, ToolRequest, TurnComplete


def executor_for(orchestration: Orchestration) -> ToolExecutor:
    ex = ToolExecutor({}, ())
    for definition in orchestration.tool_defs:
        ex.register_and_grant(definition, ToolSpec(definition.name, definition.permission or "orchestration", False))
    return ex


def local_orchestration() -> Orchestration:
    return Orchestration(ToolSettings(enabled=True, location=False, weather=False, fetch=False, research=False,
                                      background=False, search=False),
                         session_id="s1", tenant_id="t1", meeting_id="m1")


def test_live_engine_returns_a_structured_time_result():
    orchestration = local_orchestration()
    ex = executor_for(orchestration)
    eng, (s,), _, _ = mk(tools=ex, params=LiveParams(hold_ms=300, max_tool_calls_per_turn=4))

    async def go():
        async with running(eng):
            s.push(ToolCallRequested((ToolRequest("1", "get_time", {"timezone": "Africa/Lagos",
                                                                    "at": "2026-10-20T12:00"}),)))
            await until(lambda: s.tool_results)
            _, name, payload = s.tool_results[0]
            assert name == "get_time"
            assert payload["result"]["timezone"] == "Africa/Lagos"
            assert payload["result"]["utc_time"] == "2026-10-20T11:00:00+00:00"
        await orchestration.close()

    asyncio.run(go())


def test_live_engine_reports_tool_failure_without_inventing_a_result():
    orchestration = local_orchestration()
    ex = executor_for(orchestration)
    eng, (s,), _, _ = mk(tools=ex)

    async def go():
        async with running(eng):
            s.push(ToolCallRequested((ToolRequest("1", "get_time", {"timezone": "Not/AZone"}),)))
            await until(lambda: s.tool_results)
            _, name, payload = s.tool_results[0]
            assert name == "get_time" and payload["error"] == "unknown_timezone"
            assert "result" not in payload
        await orchestration.close()

    asyncio.run(go())


def test_per_turn_tool_budget_stops_a_loop_and_then_resets():
    orchestration = local_orchestration()
    ex = executor_for(orchestration)
    eng, (s,), _, _ = mk(tools=ex, params=LiveParams(hold_ms=300, max_tool_calls_per_turn=2))

    async def go():
        async with running(eng):
            calls = tuple(ToolRequest(str(i), "get_time", {"timezone": "UTC"}) for i in range(3))
            s.push(ToolCallRequested(calls))
            await until(lambda: len(s.tool_results) == 3)
            by = {cid: payload for cid, _, payload in s.tool_results}
            assert "result" in by["0"] and "result" in by["1"]
            assert by["2"]["error"] == "tool_budget_exceeded"
            assert eng.metrics.counters["live_tool_budget_denied"] == 1
            assert eng.metrics.summary("live_tool_queue_ms")["count"] == 2

            s.push(TurnComplete(1))
            await asyncio.sleep(0)
            s.push(ToolCallRequested((ToolRequest("3", "get_time", {"timezone": "UTC"}),)))
            await until(lambda: len(s.tool_results) == 4)
            assert "result" in s.tool_results[3][2]     # a new turn gets a fresh budget
        await orchestration.close()

    asyncio.run(go())


def test_background_research_is_executed_off_the_voice_path_and_retrievable():
    def transport(url: str, _timeout: float, _max: int) -> HttpResponse:
        return html_response("<html><body>Evidence</body></html>", url)

    fetcher = SafeFetcher(resolver=PUBLIC_RESOLVER, transport=transport)
    orchestration = Orchestration(ToolSettings(enabled=True, location=False, weather=False, fetch=True,
                                                research=True, background=True, search=True, search_api_key="x"),
                                  session_id="s1", tenant_id="t1", meeting_id="m1",
                                  search=FakeSearch(delay=0.05), fetcher=fetcher)
    ex = executor_for(orchestration)
    eng, (s,), _, _ = mk(tools=ex)

    async def go():
        async with running(eng):
            s.push(ToolCallRequested((ToolRequest("1", "start_research", {"query": "topic"}),)))
            await until(lambda: s.tool_results)
            payload = s.tool_results[0][2]["result"]
            task_id = payload["id"]
            assert payload["status"] in {"queued", "running"}
            assert payload["result"] is None

            await asyncio.sleep(0.2)
            s.push(ToolCallRequested((ToolRequest("2", "research_result", {"task_id": task_id}),)))
            await until(lambda: len(s.tool_results) >= 2)
            result = s.tool_results[1][2]["result"]
            assert result["result"]["session_id"] == "s1"
            assert result["result"]["sources"][0]["url"].startswith("https://")
        await orchestration.close()

    asyncio.run(go())
