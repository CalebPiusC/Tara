# AI Provider Integration

**Status: Designed, not implemented.** Research: [GEMINI_RESEARCH.md](GEMINI_RESEARCH.md).

`LanguageModelProvider` wraps Gemini via config: `TARA_LLM_PROVIDER=gemini`, `TARA_LLM_MODEL=<id>`. Never hard-code ids. Provider layer: per-call timeout, bounded retries with jitter on retryable errors only, JSON-schema output + Pydantic validation, usage metrics, no prompt/transcript logging by default. Fake provider for tests. Alternative providers implement the same protocol.
