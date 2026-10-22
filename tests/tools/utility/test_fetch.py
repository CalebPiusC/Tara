"""SSRF checks, bounded HTML extraction, and explicit page failures."""
from __future__ import annotations

import asyncio

import pytest

from tara.core.errors import ToolError
from tara.tools.utility.contracts import HttpResponse
from tara.tools.utility.fetch import SafeFetcher, fetch_tool_defs, validate_url
from tests.tools.utility.fakes import PUBLIC_RESOLVER, html_response


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/file",
    "http://127.0.0.1/",
    "http://localhost/",
    "http://[::1]/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/",
    "https://user:pass@example.com/",
    "https://example.com:8443/",
    "https://metadata.google.internal/",
])
def test_ssrf_and_unsafe_schemes_are_blocked(url):
    with pytest.raises(ToolError) as exc:
        validate_url(url)
    assert exc.value.code == "fetch_blocked"


def test_private_dns_answers_are_blocked_even_for_public_names():
    with pytest.raises(ToolError) as exc:
        validate_url("https://looks-public.example/", lambda _h, _p: [("192.168.1.2", 443)])
    assert exc.value.code == "fetch_blocked"


def test_html_extraction_is_readable_and_ignores_scripts():
    html = """<html><head><title>Report</title>
    <meta property="article:published_time" content="2026-10-01T10:00:00Z"></head>
    <body><script>ignore this</script><style>.x{}</style><h1>Title</h1><p>Useful evidence.</p></body></html>"""
    fetcher = SafeFetcher(resolver=PUBLIC_RESOLVER, transport=lambda url, _t, _b: html_response(html, url))

    async def go():
        out = await fetcher.fetch("https://example.com/page", 1000)
        assert out["title"] == "Report" and out["published"] == "2026-10-01T10:00:00Z"
        assert "Useful evidence." in out["text"] and "ignore this" not in out["text"]
        assert out["truncated"] is False

    asyncio.run(go())


def test_page_text_is_truncated_to_the_requested_budget():
    html = "<html><body>" + ("word " * 200) + "</body></html>"
    fetcher = SafeFetcher(resolver=PUBLIC_RESOLVER, transport=lambda url, _t, _b: html_response(html, url))

    async def go():
        out = await fetcher.fetch("https://example.com/page", 200)
        assert len(out["text"]) == 200 and out["truncated"] is True

    asyncio.run(go())


def test_http_error_is_reported_not_replaced_with_content():
    fetcher = SafeFetcher(resolver=PUBLIC_RESOLVER,
                          transport=lambda url, _t, _b: HttpResponse(404, {"content-type": "text/html"}, b"no", url))

    async def go():
        with pytest.raises(ToolError) as exc:
            await fetcher.fetch("https://example.com/missing")
        assert exc.value.code == "fetch_failed" and exc.value.retryable is False

    asyncio.run(go())


def test_unsupported_content_type_is_rejected():
    fetcher = SafeFetcher(resolver=PUBLIC_RESOLVER,
                          transport=lambda url, _t, _b: HttpResponse(200, {"content-type": "image/png"}, b"\x89PNG", url))

    async def go():
        with pytest.raises(ToolError) as exc:
            await fetcher.fetch("https://example.com/image.png")
        assert exc.value.code == "unsupported_content"

    asyncio.run(go())


def test_fetch_tool_marks_page_as_untrusted():
    fetcher = SafeFetcher(resolver=PUBLIC_RESOLVER,
                          transport=lambda url, _t, _b: html_response("<html><body>text</body></html>", url))
    definition = fetch_tool_defs(fetcher)[0]

    async def go():
        out = await definition.handler("https://example.com/page", 200)
        assert "untrusted" in out["note"] and out["final_url"] == "https://example.com/page"

    asyncio.run(go())
