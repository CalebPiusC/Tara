"""StdlibHttpClient plumbing: bounded JSON POST with no real network."""
from __future__ import annotations

import asyncio
import json

from tara.orchestration.http import StdlibHttpClient


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}
    url = "https://api.example/search"

    def __init__(self, body: bytes):
        self._body = body

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


def test_post_sends_bounded_json_and_does_not_expose_the_key(monkeypatch):
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout=None):  # noqa: ANN001 - test double for urllib
        captured["method"] = req.get_method()
        captured["body"] = req.data
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["timeout"] = timeout
        return FakeResponse(b'{"ok": true}')

    monkeypatch.setattr("tara.orchestration.http.urllib.request.urlopen", fake_urlopen)
    response = asyncio.run(StdlibHttpClient().post(
        "https://api.example/search", json_body={"query": "q"},
        headers={"Authorization": "Bearer secret-token"}, timeout_s=3.0))

    assert response.status == 200 and response.body == b'{"ok": true}'
    assert captured["method"] == "POST" and captured["timeout"] == 3.0
    assert json.loads(captured["body"]) == {"query": "q"}          # type: ignore[arg-type]
    headers = captured["headers"]
    assert headers["authorization"] == "Bearer secret-token"        # type: ignore[index]
    assert headers["content-type"] == "application/json"            # type: ignore[index]
