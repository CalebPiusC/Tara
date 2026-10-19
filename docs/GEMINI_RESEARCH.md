# Gemini Research

Accessed 2026-10-19 (S5, S6, S7).
- **Model ids seen:** stable `gemini-3.8-flash`, `gemini-3.6-flash`; preview `gemini-3-flash-preview`; the page also recommends a Flash-Lite tier for new projects. Context window for Flash not stated on that page. Ids change; keep in config and re-check before demos.
- **Structured output:** JSON Schema subset (object/array/string/enum/min/max…), streaming yields partial JSON; combos with function calling are preview for Gemini 3 series (S7). Always re-validate.
- **Live API (S6):** WebSocket, PCM16 16 kHz in / 24 kHz out, barge-in, function calling, ephemeral tokens for client-side use. Candidate alternative to cascaded STT→LLM→TTS; trade-off: less control and no local transcript-with-attribution pipeline. Decision deferred (Q9, ADR-0002).
- **Not verified:** rate limits/quotas, retry guidance, safety settings, data retention/training terms for the chosen tier. Read the pricing and terms pages before sending real interview data.
- **Recommendation:** use a normal text endpoint with structured output for the cascaded design; confidence medium.
