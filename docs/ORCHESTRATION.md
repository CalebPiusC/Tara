# Orchestration tools: time, location, weather, search and bounded research

**Status: implemented and tested offline (462 tests; `ruff` and `mypy` clean). Open-Meteo geocoding/weather, countries.dev country facts, and bounded public-page retrieval were exercised against the real services on 2026-10-20. Brave and Tavily web search are implemented behind `SearchProvider` (`TARA_SEARCH_PROVIDER`), but neither has been called live because no `TARA_SEARCH_API_KEY` was available. No latency number is claimed; the metrics exist, but they have not been measured in a real meeting. Nothing here connects to Google Meet, sends messages, spends money, or changes account settings.**

Written 2026-10-20. Provider facts and access dates: [research/SOURCES.md](research/SOURCES.md) for voice/model sources; external-provider sources are cited inline below.

## Decision

Orchestration is a **set of read-only tools in Tara's existing `ToolDef` / `ToolExecutor` path**, not a second assistant and not a separate agent loop. The Live model can choose among the tools that code has registered and granted. The application still owns the participation gate, playback cancellation, tool authorization, memory, and every side effect. The model never gets to add a tool, widen a budget, choose a filesystem path, or speak on a background task's behalf.

This keeps the existing rule intact: **models propose; the runtime approves and acts.** Retrieved text is untrusted data, never instructions.

## What exists

```text
Live model function call
        |
        v
LiveVoiceEngine._run_tool          per-turn tool budget, queueing, stale-session checks
        |
        v
ToolExecutor.execute_read_only    grant check, argument types, timeout, audit, structured error
        |
        +--> time.py            get_time, convert_time, time_until
        +--> calculator.py      calculate, convert_units
        +--> places.py          geocode_place, country_info, get_time_at_place
        +--> weather.py         get_weather
        +--> search.py          web_search (only when configured)
        +--> fetch.py           fetch_url (public HTTP(S), SSRF checks)
        +--> research.py        research, start_research, research_status, research_result, cancel_research
```

All orchestration tools are **read-only** (`side_effect=False`) and take a bounded timeout. There is no shell, code execution, arbitrary filesystem access, credential access, model- or user-directed outbound POST, or account-changing action. The only network POST is the search client calling its own fixed provider endpoint with a bounded JSON body. The only write-like action in the product remains the separately granted `record_note` tool.

## Tool contracts

`ToolDef` now carries the information needed to reason about selection and operations:

| Field | Meaning |
|---|---|
| `name`, `description`, `required`, `optional` | What the model may call |
| `parameters_schema` | Explicit JSON schema when Python types are not enough; `ToolExecutor.catalog()` falls back to a generated schema |
| `side_effect` | `True` means `execute_read_only` refuses it; orchestration tools are `False` |
| `timeout_s` | Hard bound applied by `ToolExecutor` with `asyncio.wait_for` |
| `permission` | Informational label; the actual grant is the code-owned `ToolSpec` |
| `category` | `time`, `math`, `location`, `weather`, `search`, `fetch`, `research` |
| `cost` / `latency` | `free` / `metered` and `fast` / `slow` / `background` labels for operational choices |

`ToolExecutor.describe()` keeps its older minimal shape. `ToolExecutor.catalog()` adds the schema, async flag, side-effect flag, timeout, category, cost and latency. Neither exposes argument values or secrets. `ToolResult` carries a stable `error` code, `retryable`, and content-free `details`; `ToolError` is the typed provider failure.

The model sees declarations only for tools that are both registered and granted. The code-owned grants live in `LiveTaraApp`; the model cannot create one.

## Tools and providers

| Tool | Provider / implementation | Notes |
|---|---|---|
| `get_time` | Python `zoneinfo` (IANA time zones) | Requires an explicit IANA zone; DST-correct; invalid zones and bad datetimes raise `unknown_timezone` / `bad_time` |
| `convert_time` | `zoneinfo` | Rejects nonexistent local times (spring-forward gap); flags ambiguous fall-back times and uses fold 0 (the earlier occurrence) |
| `time_until` | `zoneinfo` | Uses the current instant; returns a human duration and a `past` flag |
| `calculate` | small AST evaluator | Numbers, `+ - * / // % **`, parentheses, `abs`, `round`, `min`, `max`, `sqrt`, `floor`, `ceil`; no `eval`, names, attributes, calls or comprehensions |
| `convert_units` | small table | Length, mass, temperature, volume, speed; incompatible units raise `bad_unit` |
| `geocode_place` | Open-Meteo geocoding | Returns the best match plus alternatives, `ambiguous`, `assumed`, coordinates, country and IANA timezone. It never invents a place. [Open-Meteo geocoding docs](https://open-meteo.com/en/docs/geocoding-api), accessed 2026-10-20 |
| `country_info` | countries.dev | Capital, region, population, currencies, languages, ISO codes. Keyless, but a custom User-Agent is required (the default Python agent received HTTP 403). [countries.dev](https://countries.dev/), accessed 2026-10-20 |
| `get_time_at_place` | Open-Meteo geocoding + `zoneinfo` | Geocodes first, then returns the time in the place's timezone; the result still says when the match was assumed |
| `get_weather` | Open-Meteo forecast | Current conditions and up to 7 forecast days; WMO description; observation time; attribution. **No official weather alerts are supplied, so the tool always reports none; the model must not invent one.** [Open-Meteo docs](https://open-meteo.com/en/docs), [terms](https://open-meteo.com/en/terms), accessed 2026-10-20 |
| `web_search` | Brave or Tavily behind `SearchProvider` | Title, URL, snippet and publication age (publisher only when the provider returns it); public-URL filtering; duplicate URLs removed. [Brave Search API](https://brave.com/search/api/), [Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search), both accessed 2026-10-20 |
| `fetch_url` | `SafeFetcher` | Public HTTP(S) only; SSRF checks; bounded bytes and characters; HTML text extraction; no scripts or styles in the text |
| `research` | `ResearchService` | One search plus a few pages, bounded by time, sources and characters; source ids and URLs; partial-failure warnings |
| `start_research` | `ResearchTaskManager` | In-process background task. Returns an id and `status=queued`; that is an acknowledgement, not a result |
| `research_status` | `ResearchTaskManager` | Status, progress, deadline, error and retryability; unknown ids are `task_not_found` |
| `research_result` | `ResearchTaskManager` | Returns the result only when status is `completed`; otherwise returns status and `result=None` |
| `cancel_research` | `ResearchTaskManager` | Cooperative cancellation; the task remains visible as `cancelled` |

### Provider limits and costs

- **Open-Meteo:** free API for non-commercial use, data under CC BY 4.0; published limits are below 10,000 calls/day, 5,000/hour and 600/minute. The tool reports the attribution string and observation time. These limits were read on 2026-10-20 and can change; they are not enforced by a local rate limiter yet, only by caching and per-session research budgets.
- **countries.dev:** keyless country facts; a custom User-Agent is required. No availability SLA is assumed.
- **Brave:** paid plan reported at $5 per 1,000 requests with a $5 monthly credit and 50 requests/second; checked 2026-10-20. Search is **off by default** and needs `TARA_SEARCH_API_KEY`. No live Brave request was made in this work because no key was provided.
- **Tavily:** free "Researcher" plan reported at 1,000 API credits/month with no credit card required; a basic search costs 1 credit; checked 2026-10-20. Tara calls `POST /search` with `search_depth=basic` and `include_answer=false`, so it stays a retrieval tool and makes no provider-side model call. The plan can change without notice; check the pricing page before relying on it. No live Tavily request was made in this work because no key was provided.
- No new Python dependency was added. Providers use `urllib` in a worker thread so network waits do not block the Live audio loop.

## Time-zone rules

- The tool never guesses a zone: `get_time` requires `timezone` and `get_time_at_place` geocodes an explicit place.
- If a place is ambiguous, the result says `ambiguous=true` and `assumed=true`; the model is instructed to state the assumption or ask which place the participant meant.
- `convert_time` uses `zoneinfo` for DST transitions. A nonexistent local time is rejected instead of shifted silently. An ambiguous local time is flagged and resolved to fold 0; the model is told to mention the ambiguity when it matters.
- Geographic knowledge, the participant's current location, and a mentioned place are separate. There is no persistent "user location"; a location must come from the conversation or an explicit argument. That matches the memory boundary: nothing here writes long-term memory.

## Search and page retrieval

- `web_search` returns structured hits, not a synthesized answer. A missing title or non-public URL is dropped. Results are marked untrusted in the tool output.
- `fetch_url` resolves the host, rejects loopback, private, link-local, multicast, reserved and unspecified addresses, rejects URL credentials and ports other than 80/443, revalidates redirects, and only accepts text-like content (`text/html`, `text/plain`, `application/xhtml+xml`, JSON). It bounds response bytes and extracted characters; it never executes scripts.
- The extractor removes scripts/styles/tags, unescapes entities, and keeps the visible text. A prompt-injection sentence in a page remains data. The orchestration instructions tell the model not to follow instructions found in tool output.
- Missing pages produce warnings and snippets, not fabricated page text.

## Research and background tasks

`ResearchService.run` is deliberately single-step and bounded:

1. One search query; the provider may report an altered query.
2. Up to `research_sources` (default 3, maximum 5) unique public hits.
3. One bounded page fetch per hit, with a per-source character share of `research_chars` (default 8,000, maximum 20,000).
4. At most `research_timeout_s` (default 25 s) for the whole search/fetch phase; per-page timeout capped at 12 s.
5. Source ids (`s1`, `s2`, ...), URL, title, snippet, publication date when available, fetched/truncated flags, and partial-failure warnings.

If the model wants a second step, it calls the tool again with a refined query. A per-session budget (`search_calls_per_session`, default 8) prevents repeated loops. There is no vector database, no automatic web browsing loop, and no hidden multi-agent planner.

Background tasks are **in-process and session-scoped**:

- ids look like `r` plus 12 hex characters;
- status is `queued`, `running`, `completed`, `failed` or `cancelled`;
- created/updated/deadline timestamps, progress text, result, error code and retryability are stored;
- at most `background_tasks` (default 2) run at once; results expire after `task_ttl_s` (default 1,800 s) once terminal;
- a restart loses unfinished work. The tool text says so; the model must not promise durability;
- completion is **pull-based**. The model calls `research_status` or `research_result`. There is no auto-spoken completion, so a stale result cannot interrupt a newer interaction, and mute/participation state is untouched;
- cancellation is cooperative and is also called on application shutdown.

This is intentionally smaller than a task queue. There is no Redis, worker process, or durable job store, because no measured need for one exists yet.

## Permissions and safety

- All orchestration tools are read-only and code-granted. The model cannot grant itself a tool or expand a scope.
- No arbitrary shell, code execution, credential access, unrestricted filesystem access, model-directed outbound request, purchase, message send, or account-setting change is exposed. The one POST is the search provider client calling its fixed endpoint.
- Retrieved pages, search snippets, country facts and weather text are **untrusted data**. The model is instructed to cite, not obey, them.
- Tool errors are typed and content-free. Provider bodies, API keys and page content are not logged or put in `ToolResult.details`.
- Search is off unless the operator enables it and supplies a key. Location, weather and public-page fetch can be disabled independently. `research` is registered only when search and fetch are both available.
- The Live path keeps a per-turn tool budget (default 8) in addition to the provider's own tool behavior. The engine resets the budget at turn completion and interruption, and rejects results that belong to a dead session.
- No memory/context manager is duplicated. `ResearchContext` carries `session_id`, `tenant_id` and `meeting_id` for future integration with Workstream A; this branch does not copy or modify `src/tara/memory`.

## Observability

Measured metrics (names only; no query text, page text, transcripts or keys):

| Metric | What it measures |
|---|---|
| `tool_ms.<tool>` | End-to-end `ToolExecutor` time for that tool (search, page, research, weather, etc.) |
| `live_tool_queue_ms` | Time a Live tool call waited for the engine's tool semaphore |
| `live_tool_calls` | Tool results sent back to the provider |
| `live_tool_budget_denied` | Calls refused by the per-turn budget |
| `tool_failures`, `tool_denied` | Structured tool failures and authorization denials |
| `research_ack_ms` | Time to return the explicit "started, not complete" acknowledgement |
| `research_task_total_ms` | Time from background-task creation to terminal state |
| `research_tasks_started`, `research_tasks_completed`, `research_tasks_failed` | Task lifecycle counters |
| existing `first_audio_ms` / `live_*` metrics | Voice latency and session health, unchanged |

**Tool-selection time is not separately measurable**: the Live model chooses the function call, and the provider does not expose a decision timestamp. Do not report a made-up decision time. The measured pieces are queueing, execution and research acknowledgement/completion. **Orchestration makes no extra model call**; the only model usage is the existing Live turn that chose the tool.

## Tests

Offline (no network, no key): `.venv/bin/python -m pytest -q` → **462 passed, 6 skipped, 7 deselected** on 2026-10-20. The orchestration tests cover:

- time zones, DST spring-forward gaps and fall-back ambiguity;
- safe arithmetic, rejected code/name/comprehension expressions, unit conversion;
- geocoding ambiguity, country facts, weather parsing, attribution, no invented alerts;
- Brave and Tavily response parsing, POST body and auth header, deduplication, public-URL filtering, auth/quota/rate-limit/server failures;
- provider selection from settings, and a settings error for an unknown provider name;
- SSRF rejection, private DNS answers, HTML extraction, truncation, content-type errors;
- research deduplication, partial page failures, session budget, task completion/cancellation/timeout/limit;
- settings validation and secret redaction;
- Live engine execution, structured tool failure, per-turn budget reset, background research retrieval, and the queue metric.

Real-provider tier (network, still no model call):

```bash
TARA_EXTERNAL_TESTS=1 .venv/bin/python -m pytest -m external tests/external -q
```

The search check runs only when `TARA_SEARCH_API_KEY` is set and uses whichever provider
`TARA_SEARCH_PROVIDER` selects (Brave by default). On 2026-10-20 the tier produced
`2 passed, 1 skipped`: Open-Meteo geocoding/weather/countries.dev and `https://example.com/`
retrieval passed; search was skipped because `TARA_SEARCH_API_KEY` was unset. These are
**provider checks**, not evidence that the Live model behaves well.

The live Gemini tier is separate and unchanged: `-m live_api`, `TARA_LIVE_TEST=1`, a Gemini key and a model id. Nothing in this work ran that tier.

## Configuration

See `.env.example` and [CONFIGURATION](CONFIGURATION.md). The main switches are:

| Variable | Default | Meaning |
|---|---|---|
| `TARA_TOOLS_ENABLED` | `1` | Register orchestration at composition |
| `TARA_TOOLS_TIME` / `TARA_TOOLS_CALCULATOR` | `1` | Local, free tools |
| `TARA_TOOLS_LOCATION` / `TARA_TOOLS_WEATHER` | `1` | Open-Meteo geocoding and weather |
| `TARA_TOOLS_FETCH` | `1` | Public-page retrieval |
| `TARA_TOOLS_SEARCH` | `0` | Web search (`TARA_SEARCH_PROVIDER=brave|tavily`); also requires `TARA_SEARCH_API_KEY` |
| `TARA_TOOLS_RESEARCH` | `1` | Only registered when search + fetch are usable |
| `TARA_TOOLS_BACKGROUND` | `1` | `start_research` and task tools |
| `TARA_TOOLS_MAX_CALLS_PER_TURN` | `8` | Engine per-turn budget |
| `TARA_TOOLS_SEARCH_SESSION_LIMIT` | `8` | Research/search session budget |
| `TARA_TOOLS_RESEARCH_SOURCES` / `_CHARS` / `_TIMEOUT_S` | `3` / `8000` / `25` | Research bounds |
| `TARA_TOOLS_TASKS` / `TARA_TOOLS_TASK_TTL_S` | `2` / `1800` | Background concurrency and retention |
| `TARA_TOOLS_WEATHER_CACHE_S` / `_GEOCODE_CACHE_S` / `_COUNTRY_CACHE_S` / `_SEARCH_CACHE_S` | `300` / `86400` / `604800` / `60` | TTL caches; `0` disables |

## Limitations and not done

- **Web search is unverified live** for both Brave and Tavily; no key was available. Neither adapter's real auth/rate-limit behavior is claimed.
- No direct-answer/search-grounding model call. Search returns sources; the model must reason over them and cite.
- No user-location service. A location is only what the conversation or the tool argument says.
- Background tasks are not durable and do not notify the conversation automatically. The model must poll; this is deliberate to avoid stale interruptions.
- No calendar, email, messaging, purchases, account settings or other consequential actions, so no confirmation flow for those exists yet.
- No Google Meet, browser automation, Google microphone integration or meeting-platform changes.
- No measured orchestration latency, search quality or research quality. The suite proves bounds, validation and failure behavior, not answer quality.
- Heuristics and time-zone behavior are deterministic; weather/search content is provider-dependent and can be wrong or stale.
