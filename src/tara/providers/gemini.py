"""Gemini REST provider (cloud). UNVERIFIED against a live key in this repo.

Request shape follows ai.google.dev/api/generate-content as fetched 2026-10-19:
POST .../v1beta/models/{model}:generateContent, body contents/systemInstruction/
generationConfig. The API key is sent in the `x-goog-api-key` header (header name
not stated on the fetched page: verify) so it never appears in URLs or logs.
Model id comes from config, never from code.
"""
from __future__ import annotations

import asyncio
import json
import random
import urllib.error
import urllib.request
from typing import Any, Callable

from ..contracts import LlmRequest
from ..errors import MalformedOutput, ProviderError, ProviderTimeout

BASE = "https://generativelanguage.googleapis.com/v1beta"
RETRYABLE = {429, 500, 502, 503, 504}

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


def _urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https host
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def build_body(req: LlmRequest, schema: dict[str, Any] | None) -> dict[str, Any]:
    gen: dict[str, Any] = {"temperature": req.temperature}
    if req.max_output_tokens:
        gen["maxOutputTokens"] = req.max_output_tokens
    if schema is not None:
        gen["responseMimeType"] = "application/json"
        gen["responseSchema"] = schema
    return {"systemInstruction": {"parts": [{"text": req.system}]},
            "contents": [{"role": "user", "parts": [{"text": req.user}]}],
            "generationConfig": gen}


def extract_text(payload: dict[str, Any]) -> str:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, TypeError) as e:
        raise MalformedOutput("no candidates/text in response") from e


class GeminiProvider:
    def __init__(self, api_key: str, model: str, timeout_s: float = 20.0, max_retries: int = 2,
                 max_parse_retries: int = 1, transport: Transport | None = None, backoff_s: float = 0.4,
                 grace_s: float = 1.0):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is empty")
        if not model:
            raise ValueError("model id is empty (set TARA_LLM_MODEL)")
        self._key, self.model = api_key, model
        self.timeout_s, self.max_retries, self.max_parse_retries = timeout_s, max_retries, max_parse_retries
        self._transport = transport or _urllib_transport
        self.backoff_s, self.grace_s = backoff_s, grace_s

    def __repr__(self) -> str:  # never leak the key
        return f"GeminiProvider(model={self.model!r})"

    async def _call(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{BASE}/models/{self.model}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._key}
        data = json.dumps(body).encode()
        for attempt in range(self.max_retries + 1):
            try:
                status, raw = await asyncio.wait_for(
                    asyncio.to_thread(self._transport, url, headers, data, self.timeout_s), self.timeout_s + self.grace_s)
            except (asyncio.TimeoutError, TimeoutError) as e:
                err: ProviderError = ProviderTimeout("gemini timeout")
                err.__cause__ = e
            except OSError as e:
                err = ProviderError(f"network error: {type(e).__name__}")
            else:
                if status == 200:
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError as e:
                        raise MalformedOutput("response is not JSON") from e
                if status not in RETRYABLE:
                    raise ProviderError(f"gemini http {status}")  # body omitted on purpose
                err = ProviderError(f"gemini http {status}")
            if attempt == self.max_retries:
                raise err
            await asyncio.sleep(self.backoff_s * (2 ** attempt) * (0.5 + random.random()))
        raise AssertionError("unreachable")  # pragma: no cover

    async def generate(self, req: LlmRequest, parse: Callable[[Any], Any], schema: dict[str, Any] | None = None) -> Any:
        body = build_body(req, schema)
        last: MalformedOutput | None = None
        for _ in range(self.max_parse_retries + 1):
            payload = await self._call(body)
            try:
                text = extract_text(payload)
                try:
                    value: Any = json.loads(text) if schema is not None else text
                except json.JSONDecodeError as e:
                    raise MalformedOutput("model output is not valid JSON") from e
                return parse(value)
            except MalformedOutput as e:
                last = e
        assert last is not None
        raise last
