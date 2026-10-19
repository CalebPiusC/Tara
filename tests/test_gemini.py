import asyncio
import json
import pytest
from tara.contracts import LlmRequest
from tara.errors import MalformedOutput, ProviderError, ProviderTimeout
from tara.providers.gemini import GeminiProvider, build_body, extract_text

run = asyncio.run
REQ = LlmRequest("sys", "user", 0.1, 50)
SECRET = "SECRET-KEY-123"


def ok(text):
    return 200, json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode()


class T:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body)))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def prov(t, **kw):
    return GeminiProvider(SECRET, "model-x", transport=t, backoff_s=0.0, **kw)


def test_request_shape_and_key_in_header_not_url():
    t = T(ok('{"a": 1}'))
    assert run(prov(t).generate(REQ, lambda d: d, {"type": "object"})) == {"a": 1}
    url, headers, body = t.calls[0]
    assert url.endswith("/models/model-x:generateContent") and SECRET not in url
    assert headers["x-goog-api-key"] == SECRET
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["maxOutputTokens"] == 50


def test_no_schema_means_plain_text():
    assert "responseMimeType" not in build_body(REQ, None)["generationConfig"]
    assert run(prov(T(ok("hello"))).generate(REQ, lambda s: s.upper())) == "HELLO"


def test_retries_503_then_succeeds_and_bounded():
    t = T((503, b"x"), (429, b"x"), ok('{"a": 2}'))
    assert run(prov(t, max_retries=2).generate(REQ, lambda d: d, {})) == {"a": 2}
    t2 = T((503, b"x"), (503, b"x"), (503, b"x"), (503, b"x"))
    with pytest.raises(ProviderError):
        run(prov(t2, max_retries=2).generate(REQ, lambda d: d, {}))
    assert len(t2.calls) == 3          # bounded: 1 try + 2 retries


def test_non_retryable_status_fails_fast_and_hides_body():
    t = T((400, b"leaky body with " + SECRET.encode()))
    with pytest.raises(ProviderError) as e:
        run(prov(t).generate(REQ, lambda d: d, {}))
    assert SECRET not in str(e.value) and len(t.calls) == 1


def test_network_error_and_timeout_mapping():
    with pytest.raises(ProviderError):
        run(prov(T(OSError("down")), max_retries=0).generate(REQ, lambda d: d, {}))

    def slow(url, headers, body, timeout):
        import time; time.sleep(0.3); return ok("{}")
    p = GeminiProvider(SECRET, "m", timeout_s=0.01, grace_s=0.0, max_retries=0, transport=slow, backoff_s=0.0)
    with pytest.raises(ProviderTimeout):
        run(p.generate(REQ, lambda d: d, {}))


def test_malformed_output_retried_once_then_raises():
    t = T(ok("not json"), ok('{"a": 3}'))
    assert run(prov(t).generate(REQ, lambda d: d, {})) == {"a": 3}
    t2 = T(ok("nope"), ok("still nope"))
    with pytest.raises(MalformedOutput):
        run(prov(t2).generate(REQ, lambda d: d, {}))
    assert len(t2.calls) == 2


def test_parse_failure_counts_as_malformed():
    def parse(d):
        raise MalformedOutput("bad shape")
    with pytest.raises(MalformedOutput):
        run(prov(T(ok("{}"), ok("{}"))).generate(REQ, parse, {}))


def test_empty_candidates_and_bad_http_json():
    with pytest.raises(MalformedOutput):
        extract_text({"candidates": []})
    with pytest.raises(MalformedOutput):
        run(prov(T((200, b"<html>"))).generate(REQ, lambda d: d, {}))


def test_config_validation_and_repr_hides_key():
    with pytest.raises(ValueError):
        GeminiProvider("", "m")
    with pytest.raises(ValueError):
        GeminiProvider("k", "")
    assert SECRET not in repr(prov(T()))
