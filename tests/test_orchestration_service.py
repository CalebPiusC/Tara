"""Composition, settings, metadata, and structured tool failures."""
from __future__ import annotations

import asyncio
import json

from orchestration_fakes import FakeSearch

from tara.config import ToolSpec
from tara.contracts import ToolCall
from tara.errors import ToolError
from tara.metrics import Metrics
from tara.orchestration import Orchestration
from tara.orchestration.contracts import CountryFacts
from tara.orchestration.http import StdlibHttpClient
from tara.orchestration.places import OpenMeteoGeocoder
from tara.orchestration.weather import OpenMeteoWeather
from tara.settings import RuntimeSettings, ToolSettings, validate
from tara.tools import ToolDef, ToolExecutor

LIVE = {"TARA_LIVE_ENABLED": "1", "GEMINI_API_KEY": "SECRET-123", "TARA_LIVE_MODEL": "some-live-model"}


class FakeCountry:
    async def lookup(self, name: str) -> CountryFacts:
        return CountryFacts(name=name, capital="Test City")


def test_tool_settings_validate_search_key_and_redact_it():
    errors = {i.field for i in validate(RuntimeSettings.from_env({**LIVE, "TARA_TOOLS_SEARCH": "1"}))
              if i.severity == "error"}
    assert "TARA_SEARCH_API_KEY" in errors
    ok = RuntimeSettings.from_env({**LIVE, "TARA_TOOLS_SEARCH": "1", "TARA_SEARCH_API_KEY": "search-secret"})
    assert ok.tools.search is True
    assert "search-secret" not in repr(ok) and "search-secret" not in json.dumps(ok.redacted())
    assert ok.redacted()["tools"]["search_api_key"] == "set"


def test_tool_settings_reject_bad_urls_and_limits():
    env = {**LIVE, "TARA_TOOLS_WEATHER_URL": "http://insecure.example",
           "TARA_TOOLS_RESEARCH_SOURCES": "9", "TARA_TOOLS_MAX_CALLS_PER_TURN": "0"}
    errors = {i.field for i in validate(RuntimeSettings.from_env(env)) if i.severity == "error"}
    assert {"TARA_TOOLS_WEATHER_URL", "TARA_TOOLS_RESEARCH_SOURCES", "TARA_TOOLS_MAX_CALLS_PER_TURN"} <= errors


def test_orchestration_registers_only_configured_tools_and_documents_limits():
    settings = ToolSettings(enabled=True, search=False, fetch=True, research=False, background=True)
    fake_http = StdlibHttpClient()
    geocoder = OpenMeteoGeocoder(fake_http, base_url="https://geo.example")
    weather = OpenMeteoWeather(fake_http, base_url="https://weather.example")
    orchestration = Orchestration(settings, session_id="s", tenant_id="t", meeting_id="m",
                                  geocoder=geocoder, weather=weather, country=FakeCountry())
    names = {d.name for d in orchestration.tool_defs}
    assert {"get_time", "calculate", "get_weather", "fetch_url"} <= names
    assert "web_search" not in names and "start_research" not in names
    assert "start_research only means" in orchestration.instructions()
    assert orchestration.health()["search"] == "off"


def test_orchestration_with_search_exposes_search_and_background_tasks():
    settings = ToolSettings(enabled=True, search=True, fetch=True, research=True, background=True,
                            search_api_key="not-used-here")
    orchestration = Orchestration(settings, session_id="s", tenant_id="t", meeting_id="m",
                                  search=FakeSearch(), fetcher=None)
    names = {d.name for d in orchestration.tool_defs}
    assert {"web_search", "fetch_url", "research", "start_research"} <= names
    assert orchestration.health()["search"] == "configured"
    assert orchestration.tasks is not None
    asyncio.run(orchestration.close())


def test_catalog_has_schema_async_and_cost_metadata_without_secrets():
    orchestration = Orchestration(ToolSettings(enabled=True, search=False, fetch=False, research=False,
                                                location=False, weather=False),
                                  session_id="s", tenant_id="t", meeting_id="m")
    ex = ToolExecutor({}, (), Metrics())
    for definition in orchestration.tool_defs:
        ex.register_and_grant(definition, ToolSpec(definition.name, definition.permission or "orchestration", False))
    catalog = {entry["name"]: entry for entry in ex.catalog()}
    assert catalog["get_time"]["read_only"] is True
    assert catalog["get_time"]["async"] is False
    assert catalog["get_time"]["schema"]["properties"]["timezone"]["type"] == "STRING"
    assert catalog["get_time"]["timeout_s"] == 3.0
    assert "search_api_key" not in json.dumps(catalog)
    asyncio.run(orchestration.close())


def test_tool_executor_turns_tool_error_into_structured_result():
    async def handler() -> dict[str, str]:
        raise ToolError("search_auth", "credentials rejected", retryable=False, details={"provider": "search"})

    ex = ToolExecutor({"t": ToolDef("t", {}, handler, side_effect=False)},
                      (ToolSpec("t", "read", False),))
    result = asyncio.run(ex.execute_read_only(ToolCall("t", {}), "c"))
    assert result.ok is False and result.error == "search_auth"
    assert result.retryable is False and result.details["provider"] == "search"
