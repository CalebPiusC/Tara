# ADR-0014: Orchestration as read-only tools in the existing Live path

- **Status:** Accepted (implemented offline; Brave live call unverified)
- **Date:** 2026-10-20

## Context

Tara needs time-zone-aware time, unit/arithmetic help, locations, weather, web search, page retrieval and bounded research. The Live path already has provider-native function calling and a permission-gated `ToolDef` / `ToolExecutor`. A separate agent loop would duplicate authorization, budgets, cancellation and memory boundaries, and would make it harder to prove who may speak or act.

## Decision

1. Add orchestration as a package of **read-only tools** registered by the composition root into the existing `ToolExecutor`. The Live model chooses among granted tools; code grants, validates, times out, audits and returns structured results.
2. Keep every tool `side_effect=False`. Do not expose shell, arbitrary code/file-system access, credentials, POST requests or account changes. Consequential actions would need a separate approval design.
3. Use provider interfaces:
   - Open-Meteo for geocoding and current/forecast weather; no weather alerts are claimed.
   - countries.dev for keyless country facts with an explicit User-Agent.
   - Brave Search behind `SearchProvider`; search is opt-in and keyed.
   - A bounded `SafeFetcher` with SSRF checks for public pages.
4. Treat all retrieved content as untrusted data. Never execute page instructions; never put page text or keys in logs/events.
5. Keep research single-step and bounded; multi-step means another explicit model tool call with a session budget. Background tasks are in-process, session-scoped, pull-based and non-durable. No Redis/worker/vector store.
6. Do not duplicate Workstream A's memory or context manager. Carry `session_id`, `tenant_id` and `meeting_id` provenance in research results for a future adapter.
7. Measure only what is measured: tool latency, Live tool queueing, research acknowledgement and total task time, plus existing voice metrics. Do not invent a tool-selection or answer-latency number. Orchestration adds no model call.

## Consequences

- The model can answer current questions without every request paying for a search or a second model call.
- A search key is required for web search; without it, the model must say it cannot search rather than fabricate a result.
- No durable tasks, notifications, direct-answer synthesis, user-location service, Meet integration or consequential actions exist yet.
- SSE/streaming search is not used; HTTP requests run off the audio loop with bounded timeouts and response sizes.

## Sources

- Open-Meteo docs and terms, accessed 2026-10-20: <https://open-meteo.com/en/docs>, <https://open-meteo.com/en/terms>
- countries.dev, accessed 2026-10-20: <https://countries.dev/>
- Brave Search API, accessed 2026-10-20: <https://brave.com/search/api/>
- Python `zoneinfo` / IANA time-zone database, accessed 2026-10-20.
