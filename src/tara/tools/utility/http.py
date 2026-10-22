"""Small, dependency-free HTTP helpers for read-only tools.

Requests run in a worker thread so no network wait blocks the Live audio loop.  The
client is not a general-purpose proxy: providers supply fixed base URLs and this
module bounds response size and redacts transport failures.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from ...core.errors import ToolError
from .contracts import HttpResponse

DEFAULT_USER_AGENT = "Tara/0.0 (local voice assistant)"
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def clean_text(text: str, limit: int = 4000) -> str:
    """Strip markup and collapse whitespace; never interpret the content."""
    value = html.unescape(_TAG.sub(" ", str(text or "")))
    value = _SPACE.sub(" ", value).strip()
    return value[: max(0, limit)]


def parse_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ToolError("bad_response", "The provider returned invalid JSON", retryable=True) from exc


class TTLCache:
    """A bounded, small cache. Values are immutable provider responses."""

    def __init__(self, max_entries: int = 128, clock: Callable[[], float] = time.monotonic):
        self.max_entries = max(1, max_entries)
        self._clock = clock
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= self._clock():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        if ttl_s <= 0:
            return
        if len(self._data) >= self.max_entries and key not in self._data:
            self._data.pop(next(iter(self._data)), None)
        self._data[key] = (self._clock() + ttl_s, value)

    def clear(self) -> None:
        self._data.clear()


class StdlibHttpClient:
    """`HttpClient` implementation backed by urllib, run off the event loop."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT):
        self.user_agent = user_agent

    async def get(self, url: str, *, params: Mapping[str, Any] | None = None,
                  headers: Mapping[str, str] | None = None, timeout_s: float = 8.0,
                  max_bytes: int = 1_000_000) -> HttpResponse:
        return await asyncio.to_thread(self._get_sync, url, params or {}, headers or {}, timeout_s, max_bytes)

    async def post(self, url: str, *, json_body: Mapping[str, Any] | None = None,
                   headers: Mapping[str, str] | None = None, timeout_s: float = 8.0,
                   max_bytes: int = 1_000_000) -> HttpResponse:
        return await asyncio.to_thread(self._post_sync, url, json_body or {}, headers or {}, timeout_s, max_bytes)

    def _get_sync(self, url: str, params: Mapping[str, Any], headers: Mapping[str, str],
                  timeout_s: float, max_bytes: int) -> HttpResponse:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        target = f"{url}{'&' if '?' in url else '?'}{query}" if query else url
        hdrs = {"User-Agent": self.user_agent, "Accept": "application/json", **dict(headers)}
        return self._send_sync(urllib.request.Request(target, headers=hdrs, method="GET"), timeout_s, max_bytes, target)

    def _post_sync(self, url: str, json_body: Mapping[str, Any], headers: Mapping[str, str],
                   timeout_s: float, max_bytes: int) -> HttpResponse:
        # POST is only ever issued by a provider to its own fixed endpoint with a bounded
        # JSON body; it is never driven by model text or user input directly.
        data = json.dumps(dict(json_body)).encode("utf-8")
        hdrs = {"User-Agent": self.user_agent, "Accept": "application/json",
                "Content-Type": "application/json", **dict(headers)}
        return self._send_sync(urllib.request.Request(url, data=data, headers=hdrs, method="POST"),
                               timeout_s, max_bytes, url)

    def _send_sync(self, req: urllib.request.Request, timeout_s: float, max_bytes: int,
                   target: str) -> HttpResponse:
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ToolError("response_too_large", "The provider response is too large")
                return HttpResponse(int(getattr(resp, "status", 200)), _headers(resp.headers), body,
                                    str(getattr(resp, "url", target)))
        except urllib.error.HTTPError as exc:
            # HTTP errors carry a useful status and sometimes a retry hint; the body is not exposed.
            try:
                body = exc.read(max_bytes + 1)
            except Exception:  # noqa: BLE001 - an unreadable error body must not mask the status
                body = b""
            return HttpResponse(int(exc.code), _headers(exc.headers), body, str(getattr(exc, "url", target)))
        except ToolError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            host = urllib.parse.urlsplit(target).hostname or "provider"
            raise ToolError("network_error", "The provider could not be reached", retryable=True,
                            details={"host": host}) from exc
        except OSError as exc:
            raise ToolError("network_error", "The provider could not be reached", retryable=True) from exc


def _headers(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if headers is None:
        return out
    for key in headers:
        try:
            out[str(key).lower()] = str(headers[key])
        except (KeyError, TypeError):
            continue
    return out
