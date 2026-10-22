"""Shared offline fakes for orchestration tests (no network, no provider keys)."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from tara.core.errors import ToolError
from tara.tools.utility.contracts import HttpResponse, SearchHit, SearchResponse

PUBLIC_RESOLVER = lambda _host, _port: [("93.184.216.34", 443)]  # documentation address; never contacted


class FakeHttp:
    def __init__(self, responses: Mapping[str, HttpResponse | Exception] | None = None):
        self.responses = dict(responses or {})
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, *, params: Mapping[str, Any] | None = None,
                  headers: Mapping[str, str] | None = None, timeout_s: float = 8.0,
                  max_bytes: int = 1_000_000) -> HttpResponse:
        self.calls.append({"method": "get", "url": url, "params": dict(params or {}),
                           "headers": dict(headers or {}), "timeout_s": timeout_s, "max_bytes": max_bytes})
        return self._respond(url)

    async def post(self, url: str, *, json_body: Mapping[str, Any] | None = None,
                   headers: Mapping[str, str] | None = None, timeout_s: float = 8.0,
                   max_bytes: int = 1_000_000) -> HttpResponse:
        self.calls.append({"method": "post", "url": url, "json": dict(json_body or {}),
                           "headers": dict(headers or {}), "timeout_s": timeout_s, "max_bytes": max_bytes})
        return self._respond(url)

    def _respond(self, url: str) -> HttpResponse:
        item = self.responses.get(url)
        if item is None:
            raise ToolError("not_found", "no fake response for " + url)
        if isinstance(item, Exception):
            raise item
        return item


def json_response(payload: Any, status: int = 200, url: str = "https://fake.example/") -> HttpResponse:
    return HttpResponse(status, {"content-type": "application/json"}, json.dumps(payload).encode(), url)


def html_response(html: str, url: str = "https://example.com/page") -> HttpResponse:
    return HttpResponse(200, {"content-type": "text/html; charset=utf-8"}, html.encode(), url)


class FakeSearch:
    def __init__(self, hits: tuple[SearchHit, ...] | None = None, *, delay: float = 0.0,
                 provider: str = "fake", exc: Exception | None = None):
        self.hits = hits if hits is not None else (
            SearchHit("First", "https://example.com/a", "snippet one", "2026-01-01", "Example"),
            SearchHit("Second", "https://example.org/b", "snippet two", "", "Other"),
        )
        self.delay, self.provider, self.exc = delay, provider, exc
        self.queries: list[tuple[str, int]] = []

    async def search(self, query: str, limit: int = 5) -> SearchResponse:
        import asyncio

        self.queries.append((query, limit))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return SearchResponse(query, self.hits[:limit], self.provider)
