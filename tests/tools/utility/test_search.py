"""Search adapters (Brave and Tavily): attributable results and explicit failure modes."""
from __future__ import annotations

import asyncio

import pytest

from tara.core.errors import ToolError
from tara.tools.utility.contracts import HttpResponse
from tara.tools.utility.search import (
    BraveSearchProvider,
    NullSearchProvider,
    TavilySearchProvider,
    make_search_provider,
    search_tool_defs,
)
from tests.tools.utility.fakes import FakeHttp, json_response

URL = "https://search.example/web/search"
TAVILY_URL = "https://api.tavily.example/search"


def payload() -> dict:
    return {"web": {"results": [
        {"title": "A <b>result</b>", "url": "https://example.com/a", "description": "<b>snippet</b>",
         "page_age": "2 days ago", "profile": {"name": "Example"}},
        {"title": "", "url": "https://example.com/empty"},                 # no title: ignored
        {"title": "Private", "url": "http://127.0.0.1/admin"},             # non-public: ignored
        {"title": "Second", "url": "https://example.net/c", "description": "other"},
        {"title": "Duplicate", "url": "https://example.com/a"},            # duplicate URL: deduplicated
    ]}, "query": {"altered": "a better query"}}


def test_brave_results_are_structured_and_sanitized():
    http = FakeHttp({URL: json_response(payload())})
    provider = BraveSearchProvider(http, "secret-token", base_url="https://search.example")

    async def go():
        result = await provider.search("q", 5)
        assert result.provider == "brave" and result.altered_query == "a better query"
        assert len(result.hits) == 2
        assert result.hits[0].title == "A result" and result.hits[0].snippet == "snippet"
        assert result.hits[0].publisher == "Example"
        assert result.hits[1].url == "https://example.net/c"
        sent = http.calls[0]
        assert sent["headers"]["X-Subscription-Token"] == "secret-token"
        assert sent["params"]["q"] == "q"

    asyncio.run(go())


@pytest.mark.parametrize("status,code,retryable", [
    (401, "search_auth", False),
    (403, "search_auth", False),
    (429, "search_rate_limited", True),
    (500, "search_unavailable", True),
    (400, "search_failed", False),
])
def test_brave_failures_are_explicit_and_redacted(status, code, retryable):
    http = FakeHttp({URL: HttpResponse(status, {"content-type": "application/json"}, b"{}", URL)})
    provider = BraveSearchProvider(http, "secret-token", base_url="https://search.example")

    async def go():
        with pytest.raises(ToolError) as exc:
            await provider.search("q")
        assert exc.value.code == code and exc.value.retryable is retryable
        assert "secret-token" not in str(exc.value)

    asyncio.run(go())


def test_unconfigured_search_never_returns_results():
    async def go():
        with pytest.raises(ToolError) as exc:
            await NullSearchProvider().search("q")
        assert exc.value.code == "search_not_configured"

    asyncio.run(go())


def test_search_tool_wraps_results_and_marks_them_untrusted():
    http = FakeHttp({URL: json_response(payload())})
    provider = BraveSearchProvider(http, "secret-token", base_url="https://search.example")
    definition = search_tool_defs(provider)[0]

    async def go():
        result = await definition.handler("q", 2)
        assert result["count"] == 2
        assert result["results"][0]["rank"] == 1 and result["results"][0]["url"] == "https://example.com/a"
        assert "untrusted" in result["note"]

    asyncio.run(go())


def tavily_payload() -> dict:
    return {"query": "q", "results": [
        {"title": "A <b>result</b>", "url": "https://example.com/a", "content": "<b>snippet</b>",
         "score": 0.9, "published_date": "Tue, 11 Mar 2025 17:00:00 GMT"},
        {"title": "", "url": "https://example.com/empty", "content": "no title"},      # no title: ignored
        {"title": "Private", "url": "http://127.0.0.1/admin", "content": "local"},     # non-public: ignored
        {"title": "Second", "url": "https://example.net/c", "content": "other", "published_date": None},
        {"title": "Duplicate", "url": "https://example.com/a", "content": "dup"},      # deduplicated
    ]}


def test_tavily_results_are_structured_and_sanitized():
    http = FakeHttp({TAVILY_URL: json_response(tavily_payload())})
    provider = TavilySearchProvider(http, "tvly-secret", base_url="https://api.tavily.example")

    async def go():
        result = await provider.search("q", 5)
        assert result.provider == "tavily" and len(result.hits) == 2
        assert result.hits[0].title == "A result" and result.hits[0].snippet == "snippet"
        assert result.hits[0].page_age == "Tue, 11 Mar 2025 17:00:00 GMT"
        assert result.hits[1].url == "https://example.net/c" and result.hits[1].page_age == ""
        sent = http.calls[0]
        assert sent["method"] == "post"
        assert sent["headers"]["Authorization"] == "Bearer tvly-secret"
        assert sent["json"]["query"] == "q" and sent["json"]["search_depth"] == "basic"
        assert sent["json"]["include_answer"] is False and sent["json"]["include_raw_content"] is False

    asyncio.run(go())


@pytest.mark.parametrize("status,code,retryable", [
    (401, "search_auth", False),
    (403, "search_auth", False),
    (429, "search_rate_limited", True),
    (432, "search_quota", False),
    (433, "search_quota", False),
    (500, "search_unavailable", True),
    (400, "search_failed", False),
])
def test_tavily_failures_are_explicit_and_redacted(status, code, retryable):
    http = FakeHttp({TAVILY_URL: HttpResponse(status, {"content-type": "application/json"}, b"{}", TAVILY_URL)})
    provider = TavilySearchProvider(http, "tvly-secret", base_url="https://api.tavily.example")

    async def go():
        with pytest.raises(ToolError) as exc:
            await provider.search("q")
        assert exc.value.code == code and exc.value.retryable is retryable
        assert "tvly-secret" not in str(exc.value)

    asyncio.run(go())


def test_search_provider_factory_selects_the_configured_provider():
    http = FakeHttp()
    assert isinstance(make_search_provider("tavily", http, "k"), TavilySearchProvider)
    assert isinstance(make_search_provider("brave", http, "k"), BraveSearchProvider)
