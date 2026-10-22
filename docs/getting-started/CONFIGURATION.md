# Configuration

Status: Proposed. Environment variables (placeholders only in `.env.example`):

| Var | Meaning |
|---|---|
| `GEMINI_API_KEY` | secret, never logged |
| `TARA_LLM_PROVIDER` / `TARA_LLM_MODEL` | provider and model id (verify at ai.google.dev/gemini-api/docs/models) |
| `TARA_STT_BACKEND` / `TARA_STT_MODEL` | e.g. `mlx_whisper` / size |
| `TARA_TTS_BACKEND` / `TARA_TTS_VOICE` | `kokoro` / voice id |
| `TARA_MODELS_DIR` | model cache directory |
| `TARA_MEETING_ADAPTER` | `local` \| `browser` \| `vendor` \| `media_api` |
| `TARA_DATA_DIR`, `TARA_RETAIN_*` | storage and retention |
| `PYTORCH_ENABLE_MPS_FALLBACK=1` | Kokoro on Apple Silicon (S8) |
Thresholds (attribution, end-of-turn, proactive) are config with documented "calibrate" notes, not constants. Meeting behavior lives in `MeetingConfig` ([MEETING_CONTEXT_MODEL.md](../meetings/MEETING_CONTEXT_MODEL.md)).

## Gemini Live (primary voice engine, 2026-10-20)
All read by `RuntimeSettings.from_env` (`src/tara/core/settings.py`); see [GEMINI_LIVE](../engine/GEMINI_LIVE.md) for meanings. `GEMINI_API_KEY` and `TARA_LIVE_MODEL` are required when `TARA_LIVE_ENABLED=1`; there is deliberately no default model id. The `TARA_STT_*`, `TARA_TTS_*`, `TARA_LOCAL_LLM_*` and `TARA_MODELS_DIR` variables belong to the dormant local path and can stay unset.

## Orchestration tools (read-only, 2026-10-20)
See [ORCHESTRATION](../tool-calls/ORCHESTRATION.md) for behavior, provider limits and limitations. All are read by `RuntimeSettings.from_env` and validation rejects malformed URLs, non-positive timeouts, out-of-range budgets and search enabled without a key.

| Var | Meaning |
|---|---|
| `TARA_TOOLS_ENABLED` | master switch; default `1` |
| `TARA_TOOLS_TIME` / `TARA_TOOLS_CALCULATOR` | local time-zone and safe arithmetic tools; default `1` |
| `TARA_TOOLS_LOCATION` / `TARA_TOOLS_WEATHER` | Open-Meteo geocoding and weather; default `1` |
| `TARA_TOOLS_FETCH` | public-page retrieval with SSRF checks; default `1` |
| `TARA_TOOLS_SEARCH` / `TARA_SEARCH_PROVIDER` / `TARA_SEARCH_API_KEY` | web search (`brave` or `tavily`); disabled by default, key required when enabled |
| `TARA_TOOLS_RESEARCH` / `TARA_TOOLS_BACKGROUND` | bounded research and in-process task tools; default `1` |
| `TARA_TOOLS_MAX_CALLS_PER_TURN` | Live per-turn tool budget; default `8` |
| `TARA_TOOLS_SEARCH_SESSION_LIMIT` | search/research calls per session; default `8` |
| `TARA_TOOLS_RESEARCH_SOURCES` / `_CHARS` / `_TIMEOUT_S` | research bounds; `3` / `8000` / `25` |
| `TARA_TOOLS_TASKS` / `TARA_TOOLS_TASK_TTL_S` | background concurrency and result retention; `2` / `1800` |
| `TARA_TOOLS_WEATHER_CACHE_S` / `_GEOCODE_CACHE_S` / `_COUNTRY_CACHE_S` / `_SEARCH_CACHE_S` | TTL caches in seconds; `0` disables |
| `TARA_TOOLS_WEATHER_URL` / `_GEOCODING_URL` / `_COUNTRY_URL` | provider base URLs; must be `https://` |
| `TARA_TOOLS_USER_AGENT` | provider User-Agent; countries.dev rejects the default Python agent |

`TARA_SEARCH_API_KEY` is redacted from `RuntimeSettings.redacted()` and is never placed in tool errors, events or logs. No orchestration tool has a side effect; the only write-like tool remains `record_note`, granted separately.
