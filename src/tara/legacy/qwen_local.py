"""Local Qwen3-0.6B provider via mlx-lm. UNTESTED against a real model: nothing is
downloaded in this phase. All heavy imports are lazy; everything else (prompting,
think-stripping, JSON extraction, lifecycle, queueing) is unit-tested with a fake.

Unverified (docs/engine/RUNTIME_FOUNDATION.md): the exact mlx-lm sampler API and whether the
chat-template kwarg `enable_thinking` flows through the mlx tokenizer wrapper.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from ..core.contracts import LlmRequest
from ..core.errors import MalformedOutput, ModelLoadError
from ..core.metrics import LOCAL_LLM, Metrics
from ..core.models import Health, InferencePool, ModelHandle
from ..core.settings import InferenceSettings

_THINK = re.compile(r"<think>.*?</think>", re.S)
GenerateFn = Callable[[Any, list[dict[str, str]], InferenceSettings], str]


def strip_think(text: str) -> str:
    """Remove reasoning blocks (also an unterminated one) from model output."""
    text = _THINK.sub("", text)
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


def extract_json(text: str) -> Any:
    """Find the first balanced JSON object in text. Small models often add prose around it."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                esc = (c == "\\") and not esc
                if c == '"' and not esc:
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise MalformedOutput("no valid JSON object in model output")


def mlx_loader(model_ref: str) -> Callable[[], Any]:
    def load() -> Any:
        try:
            from mlx_lm import load as mlx_load  # type: ignore[import-not-found]
        except ImportError as e:
            raise ModelLoadError("mlx-lm not installed (Phase 2: pip install mlx-lm)") from e
        return mlx_load(model_ref)             # -> (model, tokenizer)
    return load


def mlx_generate(bundle: Any, messages: list[dict[str, str]], s: InferenceSettings) -> str:
    from mlx_lm import generate  # type: ignore[import-not-found]
    model, tok = bundle
    try:
        prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
                                         enable_thinking=s.enable_thinking)
    except TypeError:                           # template without that kwarg: use the soft switch
        msgs = [dict(m) for m in messages]
        if not s.enable_thinking:
            msgs[-1]["content"] += " /no_think"
        prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    kwargs: dict[str, Any] = {"max_tokens": s.local_max_tokens}
    try:
        from mlx_lm.sample_utils import make_sampler  # type: ignore[import-not-found]
        kwargs["sampler"] = make_sampler(temp=s.temperature, top_p=s.top_p, top_k=s.top_k)
    except Exception:  # noqa: BLE001 - sampler API unverified; fall back to library defaults
        pass
    return generate(model, tok, prompt=prompt, **kwargs)


class QwenLocalProvider:
    """LanguageModelProvider backed by a local model. Never raises raw library errors."""

    def __init__(self, handle: ModelHandle[Any], pool: InferencePool, settings: InferenceSettings,
                 timeout_s: float, generate_fn: GenerateFn = mlx_generate, metrics: Metrics | None = None,
                 max_parse_retries: int = 1):
        self.handle, self.pool, self.settings, self.timeout_s = handle, pool, settings, timeout_s
        self._gen, self.metrics, self.max_parse_retries = generate_fn, metrics or Metrics(), max_parse_retries

    async def generate(self, req: LlmRequest, parse: Callable[[Any], Any], schema: dict[str, Any] | None = None) -> Any:
        bundle = await self.handle.get()
        sys_text = req.system + ("\nReply with one JSON object only, no other text." if schema is not None else "")
        messages = [{"role": "system", "content": sys_text}, {"role": "user", "content": req.user}]
        last: MalformedOutput | None = None
        for _attempt in range(self.max_parse_retries + 1):
            with self.metrics.time(LOCAL_LLM):
                raw = await self.pool.run(self._gen, bundle, messages, self.settings, timeout_s=self.timeout_s)
            text = strip_think(raw)
            try:
                return parse(extract_json(text) if schema is not None else text)
            except MalformedOutput as e:
                last = e
                messages = messages + [{"role": "assistant", "content": text[:500]},
                                       {"role": "user", "content": "Invalid. Reply with valid JSON only."}]
        assert last is not None
        raise last

    def health(self) -> Health:
        return self.handle.health()
