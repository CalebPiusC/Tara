# Memory Operations (v1)

**Status: Implemented and tested against a real local PostgreSQL on 2026-10-20.** This covers explicit long-term memory only. Document RAG, embeddings, Redis, automatic consolidation and persisted meeting notes are not implemented. Design: [MEMORY_AND_RAG](MEMORY_AND_RAG.md). Decision: [ADR-0012](../adr/0012-memory-v1-postgres-fts.md).

## What it does

- Stores an explicitly requested memory (`remember_memory`) in PostgreSQL.
- Recalls relevant memories (`recall_memory`) with PostgreSQL full-text search plus a short literal fallback.
- Forgets a memory (`forget_memory`) by id or exact text, after an explicit request.
- Injects a small, deduplicated, character-capped memory block into a new Live session's system instruction.
- Enforces `tenant_id` and `user_id` inside every query; the app builds the scope, never the model.
- Keeps writes off the audio path. A write runs in the existing live tool task and returns a tool result; a failure is reported as an error.

## What it does not do

- It does not extract memories automatically from ordinary conversation.
- It does not persist `voice.memory.InMemoryMeetingMemory` notes; they still disappear on exit.
- It does not embed text or use pgvector; paraphrases may be missed.
- It does not use Redis. The app is single-process and has no measured need for a second store.
- It does not implement per-document ACLs, PostgreSQL Row-Level Security, audit logs or encryption at rest.
- It does not provide authentication. The local demo hardcodes `tenant=local` and `user=you`; a real deployment must build `MemoryScope` from authenticated identity, never from speech or model output.
- It does not prove that the real Gemini model calls the tools correctly. Only fake-backend tests have run.

## Prerequisites

- PostgreSQL running locally (tested with 16.14).
- The optional Python extra: `.venv/bin/python -m pip install -e '.[memory]'` (installs `psycopg[binary,pool]`).
- A dedicated database. Do not point `TARA_MEMORY_DATABASE_URL` at an unrelated application's database.

## Setup

```bash
# 1. create a dedicated database (owner is the local OS user)
createdb tara_memory

# 2. install the optional driver
.venv/bin/python -m pip install -e '.[memory]'

# 3. create/update the schema (idempotent)
TARA_MEMORY_DATABASE_URL=postgresql:///tara_memory .venv/bin/python -m tara.memory.migrate
```

The migration creates `memory_records` and `schema_migrations`. It does not create organizations, users, meetings, documents, ACLs or audit tables.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TARA_MEMORY_ENABLED` | `0` | Turns memory on. Off means no tools and no startup query. |
| `TARA_MEMORY_DATABASE_URL` | empty | `postgresql://...` or `postgres://...`. Required when enabled. Never logged or included in `repr`. |
| `TARA_MEMORY_SCHEMA` | empty (public) | Optional schema; the integration tests use a throwaway schema. |
| `TARA_MEMORY_SEARCH_LIMIT` | `5` | Startup block candidate count and default recall limit. |
| `TARA_MEMORY_CONTEXT_CHARS` | `1200` | Character cap for the startup block; not a model token limit. |
| `TARA_MEMORY_STARTUP_CONTEXT` | `1` | Inject recent memories when a Live session opens. |
| `TARA_MEMORY_WRITE_TIMEOUT_S` | `3` | Bound on a memory write. |
| `TARA_MEMORY_RECALL_TIMEOUT_S` | `2` | Bound on recall/startup retrieval. |

`RuntimeSettings.redacted()` reports only `database_url: set|missing`; the URL is a secret-bearing value and is not logged.

## Data model (v1)

`memory_records`:

| Column | Purpose |
|---|---|
| `id` (uuid) | Stable identifier used by `forget_memory`. |
| `tenant_id`, `user_id` | Authorization boundary; both are in every query. |
| `session_id`, `meeting_id` | Provenance, not authorization. |
| `kind` | `fact`, `preference`, `project`, `decision`, `correction`, `note`. |
| `body` | The stored text (max 4000 characters in the service). |
| `status` | `confirmed`, `candidate`, `superseded`. An explicit request is `confirmed`. |
| `source` | Required provenance string, e.g. `local-voice-demo:turn:2`. |
| `created_at`, `updated_at`, `expires_at` | Lifecycle. Expired rows are excluded from retrieval. |
| `confidence` | 0..1; explicit saves default to 1.0. |
| `supersedes_id` | Points at the older record for an explicit correction. |
| `metadata` (jsonb) | Reserved; v1 writes `{}`. |
| `deleted_at` | Soft delete; `forget_memory` hides the row and retrieval excludes it. |
| `search` (tsvector) | Generated English full-text index (`to_tsvector('english', body)`). |

Indexes: `(tenant_id, user_id, updated_at DESC)` partial on live rows, GIN on `search`, and `(tenant_id, user_id, session_id)`.

## Data flow

1. A new Live session calls `MemoryService.startup_context(scope)` once. It lists recent records, deduplicates, caps characters and appends a labeled block to the system instruction. A DB failure returns `""` and increments `memory_startup_failures`; the voice session still opens.
2. A final input transcript is observed by `MemoryWriteGate`. Only phrases such as "remember this", "save this to memory", "don't forget this" open `remember`; only "forget that", "delete the memory", etc. open `forget`.
3. The model may call `remember_memory(text, kind)`. The handler re-checks the gate, calls `MemoryService.remember`, and returns `status=saved` only after the store commit. If the gate is closed, the tool returns `PermissionDenied`; if the DB fails, the tool returns `tool_error:<Type>` and the instruction says to report failure.
4. `recall_memory(query, limit)` is read-only. It searches within the bound scope and returns ids, text, kind, source and update time. No result means the model should say it does not remember.
5. `forget_memory(memory_id|text)` requires the forget gate. It soft-deletes and returns `deleted` or `not_found`.

## Observability

Added counters: `memory_writes`, `memory_deduplicated`, `memory_recalls`, `memory_forgets`, `memory_forget_misses`, `memory_write_failures`, `memory_recall_failures`, `memory_forget_failures`, `memory_startup_failures`, `memory_startup_context_injected`, `memory_close_failures`. Gauges: `memory_recall_results`. Latency observations: `memory_write_ms`, `memory_recall_ms`, `memory_forget_ms`, `memory_startup_ms`.

No memory text, query or URL is logged. `--debug` prints only counters and event metadata.

## Measured results (2026-10-20)

Commands and results from this workspace:

```text
.venv/bin/python -m pytest -q
389 passed, 6 skipped, 4 deselected in 19.91s

TARA_TEST_DATABASE_URL=postgresql:///tara_memory_test \
  .venv/bin/python -m pytest -m db tests/memory/test_memory_postgres.py -q
6 passed in 0.21s

.venv/bin/ruff check src tests
All checks passed!

.venv/bin/mypy src
Success: no issues found in 68 source files
```

Those timings are test-suite timings, **not** a voice latency benchmark. No first-audio measurement was taken with memory enabled. The design keeps the startup query outside audio streaming and the recall query inside the tool task; measure real first-audio and recall latency on the target machine before making a latency claim.

A separate two-process durability check (two `python -c` children against the same throwaway schema) saved a memory in the first process and recalled the same id in the second process: **1 hit**. That is the evidence for cross-session durability; it does not test the Gemini model.

## Failure modes

| Failure | Behaviour |
|---|---|
| PostgreSQL down at session start | Startup block is empty; `memory_startup_failures` increments; voice continues. |
| PostgreSQL down during a save | Tool returns an error; the model is instructed not to claim the save succeeded. |
| PostgreSQL down during recall | Tool returns an error; the model must say it cannot recall, not invent. |
| Timeout | Bounded by `TARA_MEMORY_*_TIMEOUT_S`; reported as a tool error or empty startup context. |
| Memory superseded | Old row remains with `status=superseded` and its provenance; retrieval excludes it. |
| Wrong tenant/user | Query returns nothing; canary tests cover another user and another tenant. |
| Driver missing | `PostgresMemoryStore` raises `ConfigError` at construction with the install command. |

## Tests

- Offline: `tests/memory/test_memory_core.py` covers service rules, isolation, dedupe, correction, expiry, rendering, intent detection, tool authorization and failure reporting.
- Engine integration: `tests/memory/test_memory_engine.py` covers the transcript gate, tool results and startup-context injection with the fake provider.
- App wiring: `tests/voice/test_voice_app_settings.py` covers settings validation/redaction, tool registration and startup context.
- Real PostgreSQL: `tests/memory/test_memory_postgres.py` creates a throwaway schema, runs the migration, and verifies commit, fresh-store recall, isolation, correction, expiry, concurrent writes and failure honesty. It is skipped unless `TARA_TEST_DATABASE_URL` is set.

## Remaining work

1. Verify real Gemini Live tool calling for save/recall/forget with a human listening test.
2. Add automatic consolidation (candidate extraction, provenance, promotion) after turns, bounded and asynchronous.
3. Add document ingestion, chunking and a retrieval evaluation set; only then decide whether pgvector is justified.
4. Persist meeting notes and session summaries, with retention and authorization.
5. Add ACL tables/RLS and an audit log before multi-tenant use.
6. Measure startup and recall latency with memory enabled; tune `TARA_MEMORY_CONTEXT_CHARS` and search limits from measurements.
