# Changelog

Read this first if you are joining mid-stream. It says **where the project started, what changed, and what is real**. Newest entries on top. Format loosely follows Keep a Changelog. Dates are 2026 and local to the maintainer.

**Status vocabulary** (same as the docs): *Implemented* = code exists and tests ran. *Designed* = written down, no code. *Proposed* = recommendation, not agreed. *Unverified* = depends on an external fact or model nobody has run.

## How to pick up from here
1. Read [initial.md](initial.md) (product source of truth), then [docs/README.md](docs/README.md).
2. Run `python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev,live,audio,memory]" && .venv/bin/python -m pytest -q`. Expect all green (see the newest entry for the exact count; external/live tiers are opt-in).
3. Open questions: [docs/research/OPEN_QUESTIONS.md](docs/research/OPEN_QUESTIONS.md). Roadmap: [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md).
4. Rules for contributors and agents: [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md).

---

## [Unreleased]

### 2026-10-20: orchestration workstream merged into main
**Implemented and tested offline: `450 passed, 6 skipped, 7 deselected`; `ruff check src tests` and `mypy src` clean.**
- Merged `feature/orchestrator` (read-only time, location, weather, search, public-page fetch and bounded research tools plus background tasks) into `main` alongside persistent memory v1. Merge conflicts were resolved in `settings.py`, `tools.py`, `voice/app.py`, `pyproject.toml`, `.env.example`, `README.md` and the Live/behaviour/roadmap docs; both feature sets are active in one composition root.
- `tests/test_voice_app_settings.py`: memory-tool assertions no longer pin the full tool list, because orchestration tools are registered in the same executor; they now check the memory tools are present when memory is on and absent when it is off.
- **Not verified:** every unverified item named in the two entries below still stands (real Live-model tool selection, Brave search without a key, orchestration latency, real meeting behaviour).

### 2026-10-20: persistent memory v1 (PostgreSQL, explicit save/recall/forget)
**Implemented and tested. Offline suite: 389 passed, 6 skipped, 4 deselected; real PostgreSQL suite: 6 passed in a throwaway schema; `ruff check src tests` and `mypy src` clean. No real Gemini tool call has run yet, so how the live model uses the tools is unverified.**
- **Audit first:** `voice/memory.py` had working/session memory interfaces and an in-memory `MeetingMemory`; `knowledge.py` had keyword retrieval; `context_assembly.py` was used only by the dormant cascaded path; the Live path relied on the provider's session context. There was no database, migration, persistence, explicit memory command, cross-session recall or RAG in the Live path. PostgreSQL 16.14 and Redis 8.8 were installed; no Tara database existed.
- `src/tara/memory/`: new package. `models.py` defines `MemoryScope(tenant_id, user_id)`, `MemoryItem` (id, kind, text, source, session/meeting provenance, status, timestamps, expiry, confidence, supersedes, metadata), validation, tokenizer and UTC clock. `store.py` defines the `MemoryStore` protocol, `InMemoryMemoryStore` for tests and `PostgresMemoryStore` (lazy `psycopg` pool, every query filters tenant and user, soft delete, expiry, FTS + `ILIKE` fallback). `service.py` handles validation, exact dedupe, explicit supersede, bounded retrieval, startup context rendering, metrics and truthful failures. `intent.py` has a narrow English-only `MemoryWriteGate`; `tools.py` defines the three code-owned tools. `migrate.py` applies an idempotent schema.
- `src/tara/memory/migrations/001_memory.sql`: `memory_records` plus `schema_migrations`; `tsvector` generated column, GIN index, scope/recency index, provenance and kind/status checks.
- `settings.py`: `MemorySettings` (`TARA_MEMORY_*`), validation, redacted logging (the URL is reported only as set/missing). `.env.example` has placeholders only.
- `voice/app.py`: memory is off by default; when enabled it registers `remember_memory`, `recall_memory` and `forget_memory`, adds the memory rules to the live instruction, queries recent memories once at session start and appends a bounded block. A startup failure injects nothing and counts `memory_startup_failures`; voice still opens.
- `voice/engine.py`: `start(startup_context=...)` appends the block once to the provider config (and reconnects reuse it); final input transcripts update `MemoryWriteGate`; `_run_tool` now authorizes any `ToolDef(live_write=True)` tool instead of hard-coding `record_note`. `tools.py` gained `ToolDef.live_write` and `ToolExecutor.allows_live_write`. Writes still run in the existing tool task, never on the audio or playback path.
- `voice/instructions.py`: when memory is on, the instruction requires `recall_memory` for earlier-conversation questions, limits save/forget to explicit requests, forbids false success and treats recalled content as untrusted data.
- Tests: `tests/test_memory_core.py` (service, isolation canaries, dedupe, correction, expiry, rendering, intent, tool authorization, failure honesty), `tests/test_memory_engine.py` (gate, tool results, startup context), `tests/test_voice_app_settings.py` (settings, tool registration, startup injection) and `tests/test_memory_postgres.py` (real PostgreSQL: commit, fresh-store recall, isolation, correction, expiry, concurrent writes, simulated database failure).
- Docs: rewritten [MEMORY_AND_RAG](docs/MEMORY_AND_RAG.md) status and v1 flow, new [MEMORY_OPERATIONS](docs/MEMORY_OPERATIONS.md), ADR-0012, and status updates to the data model, security, context-assembly, README, local-development and `.env.example` files.
- **Not done:** real Gemini tool calling, automatic memory extraction/consolidation, document ingestion/chunking, embeddings/pgvector, hybrid retrieval, Redis, persisted meeting notes, per-turn Live context assembly, ACL tables/RLS, audit log, encryption, measured voice latency with memory on. The model must choose to call the tools; there is no deterministic save fallback.
- **Durability evidence:** beyond the tests, a two-process check saved a memory in one Python process and recalled the same id in a second process against a throwaway PostgreSQL schema (1 hit).
### 2026-10-20: orchestration tools: time, location, weather, search and bounded research
**Implemented offline (428 tests: `428 passed, 7 deselected`; ruff and mypy clean). Open-Meteo geocoding/weather, countries.dev and a public `example.com` fetch passed against the real services; Brave search is implemented but was not called live because no API key was available. No orchestration latency number is claimed. Nothing touches Google Meet, sends messages, spends money or changes account settings.**
- `src/tara/orchestration/`: new read-only tools wired into the existing `ToolDef`/`ToolExecutor` Live function-calling path, not a second agent.
  - `contracts.py`: HTTP/search/place/country/weather/research/task data contracts and protocols; recursive JSON-safe `as_dict`.
  - `http.py`: bounded async `urllib` client running in a worker thread, JSON parsing, short-text cleaning, bounded TTL cache with per-provider expiry.
  - `time.py`: `get_time`, `convert_time`, `time_until`; IANA zones via `zoneinfo`, DST-aware, rejects nonexistent local times, flags ambiguous fall-back times and uses fold 0.
  - `calculator.py`: AST-only arithmetic and unit conversion; no `eval`; rejects names, attributes, comprehensions and oversized exponents.
  - `places.py`: Open-Meteo geocoding with alternatives/`ambiguous`/`assumed`, countries.dev facts with custom User-Agent, and `get_time_at_place`.
  - `weather.py`: Open-Meteo current conditions plus up to 7 forecast days, observation time and CC BY 4.0 attribution; no alerts are invented (`alerts_supported=false`).
  - `search.py`: Brave `SearchProvider`, structured hits, public-URL filtering, duplicate removal, explicit auth/rate-limit/server failures. Search is off by default and needs `TARA_SEARCH_API_KEY`.
  - `fetch.py`: SSRF-resistant public HTTP(S) retrieval (private/loopback/link-local/reserved addresses, URL credentials, non-80/443 ports, redirect revalidation), byte and character bounds, readable-text extraction that drops scripts/styles.
  - `research.py`: one search plus bounded pages with source ids/URLs/dates, partial-failure warnings, session budget, and in-process background tasks with ids, ownership, timestamps, progress, timeout, cancellation, result/error and TTL. Completion is pull-based; there is no auto-speak and no durable queue.
  - `service.py`: `Orchestration` composition root; provider wiring from `ToolSettings`, health and close.
- `tools.py`: `ToolDef` gains `category`, `cost`, `latency`, `parameters_schema`; `ToolExecutor.catalog()` exposes schema/async/side-effect/timeout metadata while `describe()` keeps its stable minimal shape; `ToolError` becomes a structured `ToolResult` (stable code, retryable, safe details). Argument checks accept ints for float parameters.
- `errors.py`: typed `ToolError` with `code`, `retryable` and content-free `details`.
- `settings.py`: `ToolSettings`, environment parsing, URL/timeout/budget validation, search-key requirement, and redacted output.
- `voice/app.py`: builds `Orchestration` at composition when enabled, registers its tools with the notes tool, appends bounded orchestration instructions, passes the per-turn budget, closes tasks, and reports orchestration health. `voice/engine.py`: per-turn tool budget with reset at turn completion and interruption, queue latency metric, and structured error payloads; tool result size raised to 12,000 chars.
- Tests: `tests/test_orchestration_{time,calculator,weather,search,fetch,research,service}.py`, `tests/test_voice_orchestration.py`, shared fakes, and updates to `tests/test_voice_app_settings.py` for the new declarations. Opt-in real-provider tier: `tests/external/test_providers.py` (`TARA_EXTERNAL_TESTS=1`, marker `external`, excluded by default). On 2026-10-20: `2 passed, 1 skipped` (Brave skipped).
- Docs: [ORCHESTRATION](docs/ORCHESTRATION.md), [CONFIGURATION](docs/CONFIGURATION.md), [ADR-0014](docs/adr/0014-orchestration-tools.md), docs index, `.env.example`, README test count.
- **Not done / unverified:** live Brave request (no key), real Live-model tool selection and orchestration latency, direct-answer search grounding, durable background tasks, automatic task notifications, user-location service, consequential-action confirmation flow, Google Meet, browser automation.

### 2026-10-21: local microphone/speaker demo
**Implemented offline (367 tests, ruff and mypy clean). The adapter and CLI are fake-backend tested; the owner ran the headphone conversation on real hardware, and speaker output needs half-duplex `--speaker` because there is no echo cancellation. No Google Meet connection.**
- `src/tara/adapters/audio_device.py`: a single-participant `MeetingAdapter` over `sounddevice`. Input is 16 kHz mono PCM16 in 30 ms blocks, tagged with the `you` stream id; output is resampled from the model's 24 kHz to the output device's default rate. Stream close is serialized behind a write lock so an interruption can never close a stream while a write is in flight (this fixed macOS CoreAudio errors and a crash). Barge-in calls `stop_audio`, which aborts the sink immediately; late writes are not counted after a stop. The bounded event queue drops the oldest frame and counts drops. `half_duplex=True` sends silence while Tara speaks, so speaker output is not fed back; voice barge-in is disabled in that mode. The adapter advertises no platform controls, so spoken mic-mute stays application-level and cannot trap the session.
- `src/tara/voice/demo.py`: `python -m tara.voice.demo [--profile ...] [--one-on-one] [--speaker] [--debug]`. Builds the normal `LiveTaraApp` and `MeetingConfig` (`natural`, organizer `you`), keeps the participation gate, and prints configuration or device errors. The default is `on_address_only`; `--one-on-one` selects the existing `proactive_allowed` policy so the single participant can speak without saying “Tara”; `--speaker` sets half-duplex for speaker output. Headphones give full duplex. No API key or transcript is printed. `--debug` prints non-content counters and event metadata (input transcripts, response audio, gate outcome/source, send/playback errors); a compact summary prints on exit.
- `src/tara/voice/instructions.py`: the live system instruction now says to speak English unless the participant explicitly asks for another language.
- `pyproject.toml`: optional `audio` extra (`sounddevice>=0.5`). No model is downloaded.
- Tests: `tests/test_audio_device_adapter.py` (mic events, 16k→24k and 16k→device-rate resample, successful delivery, stop before start, stop during playback, half-duplex silence, output-device failure closing the mic), `tests/test_voice_demo.py` (demo config validates and does not bypass policy) and `tests/test_behaviour_core.py` (English instruction).
- Docs: [GEMINI_LIVE](docs/GEMINI_LIVE.md), [LOCAL_DEVELOPMENT](docs/LOCAL_DEVELOPMENT.md), README and roadmap carry the run commands, the English rule and the headphone/speaker caveats.
- **Owner-run real case (2026-10-21):** the headphone conversation worked (microphone capture, gate allowed, model audio played); on speakers the mic picked Tara's voice back up and the app interrupted itself, so `--speaker` was added as a half-duplex workaround. Speaker echo cancellation, real barge-in/reconnect measurements, the full live behaviour suite, Google Meet and durable memory are still not done.

### 2026-10-21: spoken commands, meeting kinds and notes
**Implemented offline (362 tests, ruff and mypy clean). One real one-turn Gemini Live smoke test now passes. A local mic/speaker adapter exists but has not run on real hardware; Google Meet is not implemented. Behaviour, event ordering under real interruption and platform capabilities remain unverified.**
- **Real smoke (2026-10-20, one text turn):** `connect_ms=1771.9`, `first_audio_ms=1039.4`, `audio_bytes=80160`, `sample_rate=24000`, `turn_complete=true`, 11 events. This verifies real connection, audio output, sample rate and text-to-first-audio latency. It does not verify audio input, playback audibility, behaviour/humour, real interruption or reconnect, listen-only persistence, tools or Meet. The smoke's first-audio number is measured from sending text, not from end of speech.
- `src/tara/control/`: deterministic English grammar (`commands.py`), a pure permission policy (`policy.py`) and persistent `ControlState` (`state.py`). Commands must address Tara near the start of the sentence; speech about her does not fire. Silence is easy (anyone may stop her talking, except candidates in interviews for persistent listen-only); resume, leave, camera, notes, confirmation and muting others need a control role and reliable identity.
- `voice/participation.py`: spoken commands and a bare "be quiet" while Tara is speaking. The bare request is an interruption for anyone and does not persist; an addressed request is authorized. Verdicts are `COMMAND`, `MUTE`, `TAKEOVER` or `DENY`; the model never decides a command.
- `voice/engine.py`: `_handle_control` / `_enact` / `_execute_command` / `_refuse`; listen-only and mute persist across reconnects and override proactive policy; a recognized command turn is never answered by the model; refusals are honest about role, identity and platform capability. Camera, picture and mic calls go through the adapter and update state only on success.
- Interruption context: a local barge-in now commits the interrupted participant turn with the speaker who was talking before the new stream and starts a fresh turn. A provider `Interrupted` for a response already split locally no longer clears the interrupter's transcript. Regression tests: `test_local_barge_in_keeps_the_interrupted_turn_and_starts_a_new_turn` and `test_listen_only_survives_provider_turns_app_prompts_and_reconnect_until_resumed`.
- `contracts.py` / `adapters/local.py`: `ActionOutcome` and `can_mute_self`, `can_camera`, `can_show_image`, `can_mute_others`; the local adapter simulates controls and failures for tests. No Google Meet adapter exists, so Meet support for mic, camera or pictures is not assumed.
- Notes: `record_note` is a granted tool that runs only while `control.notes_active`, writes a candidate with mandatory provenance and optional owner/deadline. "Tara, confirm that" promotes the newest candidate decision or action item through the explicit `MeetingMemory.promote_latest`; `add()` still rejects confirmed records. `summarise_agreed` returns confirmed and unconfirmed items separately. There is no spoken summary command yet, and the only store is in-memory.
- Meeting kinds: `behaviour/meeting_kind.py` plus controller integration. Override wins; interview modes are always `interview`; urgent evidence forces `incident`; switching needs a margin, sustained leads and a cooldown; no evidence means no kind, never assumed casual; `brainstorm` may relax the playful cue but never allows laughter without a participant joke; a kind can only lower humour below the profile ceiling.
- Bugs found and fixed while getting the suite green: `_refuse` unpacked the denial reason one field too far, so metrics and refusal text were wrong; the per-session control flag blocked every later command in a reconnect-free turn; sequential per-participant streams were treated as ambiguous instead of using the most recent speaker; `yesterday Tara ...` parsed as an address; a bare interruption was being authorized as persistent control.
- Tests: `tests/test_control.py`, `tests/test_notes_control.py`, `tests/test_meeting_kind.py` and the interruption regression in `tests/test_voice_engine.py` (grammar, permission matrix, engine behaviour, adapter honesty, notes promotion, kind inference, profile ceilings, interrupted-turn attribution and listen-only persistence).
- Docs: [MEETING_CONTROLS](docs/MEETING_CONTROLS.md), [CONVERSATIONAL_BEHAVIOUR](docs/CONVERSATIONAL_BEHAVIOUR.md), AGENTS.md known-state paragraph removed, CLAUDE.md test count updated.
**Not done:** the full live behaviour suite, a real-device microphone run, real interruption/reconnect verification, real Meet verification, durable meeting memory, a spoken summary command.

### 2026-10-21: conversational behaviour system
**Implemented offline (325 tests, ruff and mypy clean). No real audio was produced: no vocal expression is verified.**
- **Research (docs, 2026-10-21):** on `gemini-3.8-live` affective dialogue is removed, proactive audio is always on (setting false errors), async tool calls are the default, `send_client_content` works all session (`turn_complete=true` interrupts), and laughter/tone/pacing controls are not documented for Live (the `<laugh>`-style tags are documented for TTS models only). Expressiveness is therefore steered by system instruction, voice choice and short mid-session notes; no non-verbal claims are made.
- `src/tara/behaviour/`: `profiles` (professional default, conversational, expressive; interviews clamped to professional), `signals` (lexical room-reading), `controller` (plan: intent, tone, expressiveness, humour, laughter allowance with cooldown, verbosity, regime persistence; guidance notes without participant words; rate limits), `checks` (stock openers, AI disclaimers, human-experience claims, forced laughter, lists, stage directions, restating, overlong, repeated openers), `expressions` (validated avatar-ready events, rate limits, sink isolation), `persona` (system-instruction text).
- Engine: behaviour observed on transcript fragments (milliseconds, `behaviour_ms`, no model call, no delay); style notes sent via `send_client_content` with `turn_complete=false`; output checks counted as metrics (names only) with brevity/variation feedback; expression events; **floor etiquette** (allowed speech waits while a participant is mid-sentence, drops if the moment passes). Style never overrides the gate (tests: jokes or expressive profile never make Tara speak when unaddressed; mute, silent policy, takeover, interruption still win).
- Config: `PersonaSettings` (`TARA_PERSONA_*`), `MeetingConfig.behaviour_profile` (validated, accepted from organizer draft), `TARA_LIVE_TOOL_BEHAVIOR`; `VoiceSessionConfig.non_blocking_tools` replaced by `tool_behavior`.
- Tests found and fixed: a response completing while a participant was speaking was dropped instead of waiting; first-turn style notes were suppressed; an empathy/serious regime mix-up.
- Opt-in `tests/live/test_behaviour_live.py` (rubric rates from transcripts; style-note-triggers-no-reply check). It cannot hear laughter or tone.
- Docs: [CONVERSATIONAL_BEHAVIOUR](docs/CONVERSATIONAL_BEHAVIOUR.md), sources L9-L10, questions Q-B1..Q-B7.
**Not done:** any real-session verification of how Tara sounds; avatar; multilingual or model-based signal detection.

**Project rule:** no AI or coding-agent attribution (co-author, "generated by") anywhere in this repository or its history.

### 2026-10-20: Gemini Live becomes the primary voice engine; local model plans removed
**Decision (owner):** use Gemini Live; **no local model is downloaded, installed or started**. `docs/PHASE2_SETUP_PLAN.md` (the download plan) was deleted, ADR-0002 superseded by ADR-0013, the local-model docs carry supersede banners. The cascaded Whisper/Qwen/Kokoro runtime and its adapters remain as dormant optional code (still tested with fakes).
**Implemented, offline (277 tests: scripted fake sessions plus the real `google-genai` types; ruff and mypy clean). NOT run against the real service: no key was available.**
- `voice/events.py`: provider-neutral session contract. `voice/gemini_live.py`: Live adapter (SDK verified from docs and by local introspection of google-genai 2.31.0): config builder, message mapper, bounded event queue, connect timeout, error classification (fatal 400/401/403/404), key redaction.
- `voice/playback.py`: bounded per-response playback with immediate cancel (sink stopped first), stale-chunk rejection, delivery accounting.
- `voice/participation.py`: gate deciding who may be heard (addressed, silent, muted, takeover by trusted role, interview identity). Shared helpers extracted from `participation.py`.
- `voice/engine.py`: uplink with backpressure; interruption by provider event and by local barge-in; held audio until the gate decides (fail silent on timeout); tool calls on separate tasks with cancellation; single-flight reconnect with resumption handle or working-memory re-seed; GoAway; session limits; metrics and events without content. Offline tests found and fixed three real bugs: cancelled streams were recorded as completed by the local adapter, playback stopped the stream before the sink, and provider response ids restarting per session made the first reply after a reconnect look stale.
- `voice/memory.py`: working memory, scoped meeting memory with mandatory provenance, scope-bound knowledge search. `voice/registry.py`: concurrent-session limit. `voice/app.py`: composition root. `voice/smoke.py` and `tests/live/`: opt-in real-API check.
- Settings: `LiveSettings` and validation; pytest marker `live_api` excluded by default; `live` extra for the SDK.
- Docs: [GEMINI_LIVE](docs/GEMINI_LIVE.md) (verified facts with sources, architecture, interruption, run steps, status, limitations), ADR-0013, sources L1-L8, questions Q-L1..Q-L9, README and agent files rewritten for the new direction.
**Not done:** any real-service run (model access, event ordering, latency, tool behavior unverified), mic/speaker adapter, Google Meet, RAG/database, speaker attribution beyond per-participant streams, avatar.

### 2026-10-20: unified runtime foundation
**Implemented against fakes** (219 offline tests; `mypy src` and `ruff check src tests` clean; suite stable over repeated runs). Nothing touched a real model, Gemini key or meeting; **no downloads or installs were done** (owner will announce Phase 2).
- `settings.py`: typed runtime settings from environment variables with validation (missing Gemini key/model/context, missing local model paths, bad limits); secrets never in repr or errors.
- `models.py`: `ModelHandle` (lazy, single instance, failure cooldown, timeout, unload), `InferencePool` (bounded concurrency and queue, timeouts, cancellation without exceeding limits), `ModelManager` (health, warm-up, idle unload, ordered shutdown).
- `providers/qwen_local.py`: Qwen3-0.6B adapter via mlx-lm (lazy imports; think-block stripping, JSON extraction with one repair retry). **Unverified against a real model.**
- `state.py`: typed `ConversationState` updated only through validated events; model-sourced events cannot change critical flags; persistable subset defined.
- `routing.py`, `respond.py`: rule-bounded routing (a small model can only choose among allowed routes), escalation policy and minimal cloud payload, response orchestrator with retrieval, read-only web search interface (no implementation), honest hedge lines.
- `context_assembly.py`: budgeted, deduplicated, labelled, delimiter-sanitized prompt assembly; refuses when mandatory sections do not fit. `conversation.py` now uses it.
- `tools.py`: typed tool definitions with descriptions, timeouts, side-effect flag; read-only execution path; audit without argument values. Side-effecting tools now only run after runtime approval (engine change).
- `metrics.py`: counters, gauges, bounded histograms, content-redacting structured log helper; engine records audio, STT, classify, route, retrieval, local/cloud LLM, TTS first chunk, queue wait, model load and total response latency, plus cancellations, interruptions, takeovers, failures.
- `app.py`: composition root (`TaraApp`) with health and clean shutdown; meeting privacy flag can veto cloud use.
- Engine: integrated state and metrics. Integration tests found and fixed a real bug: a response task cancelled before its first step never cleaned up its state.
- Tooling: ruff and mypy configured in `pyproject.toml`.
- Docs: [RUNTIME_FOUNDATION](docs/RUNTIME_FOUNDATION.md), ADR-0012, sources U1-U5, open questions Q-U1..Q-U6, a Qwen step was added to a local-model plan that has since been removed (nothing downloaded).
**Not done:** real STT/TTS/Qwen/VAD runs, live Gemini call, any latency measurement, RAG implementation, Meet, diarization, avatar, resource (RSS) metrics, persistence.

### 2026-10-19 (day 1, night): Phase 2 plan
- Proposed (later cancelled, see 2026-10-20 Gemini Live entry): a local-model download plan. Nothing was ever downloaded or installed.

### 2026-10-19 (day 1, night): engine hardening
**Implemented** (fakes only; 158 offline tests, stable over 5 repeated runs and clean under `python -X dev -W error`):
- Audio ingestion no longer waits on STT or the LLM: segments go through a bounded queue (oldest dropped and counted) handled by a worker, so barge-in is detected while a transcript is still being produced.
- A lock serializes the decision section; classification runs outside it and state is read after it.
- Tools now run only **after** runtime approval (before, a stale or taken-over proposal could still trigger a tool).
- Answers wait a bounded time for the floor instead of being dropped; deferred "floor busy" triggers are retried by `tick()` and expire.
- Boundaries: unexpected errors in generation end in silence + LISTENING (never a wedged state); malformed adapter payloads and invalid audio are dropped with events; STT timeout; playback watchdog (hung audio is `failed`, never assumed delivered); shutdown idempotent; events after shutdown ignored.
- Runtime dedupe and answered-trigger sets are bounded.
**Checks run:** `mypy src` clean (26 files); tests re-run after lint fixes: 158 pass.
**Known gaps (still true):** `process_segment` overlap flag is coarse; no persistence/restart recovery; no real adapter, so reconnect semantics are only simulated; hung `classify` calls have no timeout of their own; ruff reports 28 style findings in `src` (25 auto-fixable; safe ones applied, not re-reviewed individually).

### 2026-10-19 (day 1, late): memory and RAG design
- **Designed:** memory and RAG: [MEMORY_AND_RAG](docs/MEMORY_AND_RAG.md), [CONTEXT_ASSEMBLY](docs/CONTEXT_ASSEMBLY.md), [MEMORY_DATA_MODEL](docs/MEMORY_DATA_MODEL.md), [MEMORY_AND_RETRIEVAL_SECURITY](docs/MEMORY_AND_RETRIEVAL_SECURITY.md), [RAG_AND_MEMORY_EVALUATION](docs/RAG_AND_MEMORY_EVALUATION.md), [research](docs/research/RAG_TECHNOLOGY_EVALUATION.md), ADRs 0009-0011, roadmap track R1-R5. Paths follow the existing flat `docs/` convention instead of new subfolders. No retrieval, database or embedding code written; no SQL was run; no model downloaded.
- **Recommendation:** Postgres + pgvector (exact search first), hybrid FTS + vector with rank fusion, local bge-small embeddings by default, no reranker in v1, memory records start as candidates.
- **Unverified:** every performance and quality claim; pgvector license text; Postgres RLS behavior; Gemini embedding data retention.
- **Added:** this changelog.

### 2026-10-19 (day 1): engine code against fakes
**Implemented** (offline, 144 tests passing at the time of writing; nothing here touched a real model, API key or meeting):
- `src/tara/config.py`: `MeetingConfig` schema `tara.meeting_config/v0` and validator (app policy intersects requested tools; observe mode forces silence; group interviews need an attribution threshold).
- `src/tara/runtime.py`: 12-state machine, epochs, takeover, bounded event dedupe, proposal approval (stale epoch/age, duplicate trigger, floor busy).
- `src/tara/participation.py`: deterministic gate over a semantic intent.
- `src/tara/classify.py`: rule and LLM intent classifiers, fuzzy name hint.
- `src/tara/audio.py`, `turn.py`, `events.py`: ring buffer, resampler, VAD segmenter, end-of-turn estimate, in-process event bus.
- `src/tara/identity.py`, `evidence.py`: versioned label maps, evidence fusion with `unknown`, candidate evidence store that cannot cross candidates.
- `src/tara/context.py`, `conversation.py`, `interview.py`, `knowledge.py`, `tools.py`, `reports.py`: context engine, prompt and response validation, interview engine, keyword knowledge, permission-gated tools, reports.
- `src/tara/engine.py`: async pipeline with cancellation, barge-in, takeover, reconnect, echo guard.
- `src/tara/providers/gemini.py`: REST client, tested with a fake transport only. `providers/local_models.py`: mlx-whisper, Kokoro, Silero wrappers, **untested, nothing downloaded**.
- `src/tara/sim.py`, `fakes.py`, `adapters/local.py`: simulation harness, fakes, local scripted meeting adapter.
- Two real runtime bugs were found by the simulation scenarios and fixed (takeover while idle or generating).

**Not done / Unverified:** real STT/TTS/VAD runs, live Gemini call (request shape and auth header unconfirmed), any Google Meet connection, any latency measurement, avatar.

### 2026-10-19 (day 1): research and documentation foundation
- **Added:** about 45 documents in `docs/` (product, architecture, context model, runtime, identity, audio pipeline, contracts, security, failure modes, testing, roadmap), 8 ADRs (all *Proposed*), research notes with sources and access dates, `AGENTS.md`, `CLAUDE.md`, `.gitignore`, `.env.example`.
- **Key finding:** the official Meet Media API is Developer Preview, closed to new signups, and documents receiving media only. Realistic proof of concept is an unofficial browser participant or a third-party bot API. Spike plan written, **not run**: [docs/research/PHASE0_MEET_SPIKE.md](docs/research/PHASE0_MEET_SPIKE.md).
- **Environment facts:** Apple M4 Pro, 24 GB. System `python3` is 3.9.6 (too old); Python 3.13 is installed via Homebrew and used for `.venv`.

### 2026-10-19 (day 1): starting point
- Repository contained only `initial.md` (identical copy `INITIAL.md`), an empty `README.md` and an empty `.gitignore`. No git repository, no code.
