# Memory Operations (v2)

**Status: Implemented and tested offline plus against a real local PostgreSQL on 2026-10-23.** This covers long-term memory, automatic meeting notes, cross-meeting retrieval inside a section, consolidation, corrections, startup context, restart recovery and approved-document RAG (ingestion, chunks, versions, read ACLs, section scope, deletion and the Live tool). Metrics are suite results, not voice-latency measurements. Design: [MEMORY_AND_RAG](MEMORY_AND_RAG.md), [DOCUMENT_RAG](DOCUMENT_RAG.md). Decisions: [ADR-0012](../adr/0012-memory-v1-postgres-fts.md), [ADR-0015](../adr/0015-automatic-meeting-memory.md), [ADR-0016](../adr/0016-memory-retrieval-consolidation-recovery.md), [ADR-0017](../adr/0017-document-rag.md). Real Gemini tool calling is still unverified.

## What it does

- Stores an explicitly requested memory (`remember_memory`) in PostgreSQL, or in the in-memory store used by tests.
- Stores automatic meeting notes without being asked: SESSION-tier decisions, action items, open questions and proposals detected in a finished turn go to the live meeting memory **and** to the durable store as candidates. A process exit no longer loses them.
- Extracts transcript chunks, a summary and verified candidate facts at checkpoints and at the end; each fact must carry a verbatim quote from the transcript.
- Keeps proposals separate from decisions. "We should probably use PostgreSQL" is stored as a `note` candidate with `metadata.tentative=true`; only committed wording such as "we have decided" can produce a `decision` candidate. Candidates remain unconfirmed until explicitly promoted or seen again in a later meeting.
- Links an explicit correction to the record it replaces when the target can be identified confidently. The old row stays with `status=superseded`, `metadata.superseded_by`, its source and timestamps. Ambiguous corrections keep every candidate and set `metadata.possible_conflict` and `metadata.correction_candidates` instead of guessing.
- Consolidates only near-identical durable records (token Jaccard ≥ 0.9). Numbers are significant, so "Decision 1: ship" and "Decision 2: ship" are never merged. Merging supersedes a duplicate and records `metadata.duplicate_of`; it never deletes history.
- Retrieves across every meeting in the same section with PostgreSQL full-text search plus a deterministic Python re-ranker: keyword rarity, phrase match, confirmation status, kind, recency half-life, conflict flags and near-duplicate diversity.
- Understands spoken time filters ("today", "yesterday", "last week", "last 3 days") and kind hints ("what did we decide", "what did I promise", "summaries") and applies them before ranking. A filter with no keyword match falls back to the newest items inside that window.
- Injects a bounded, source-labelled startup block: one summary per meeting first, then corrections, decisions, open actions, other confirmed items, candidates, and only then a few transcript chunks. Meeting number and date are included when known.
- Recovers meetings a dead process left open: they are closed at their last activity, marked `interrupted=true`, and shown as "recovered after a restart". A session owned by another process that is still alive on the same host is left alone.
- Enforces `tenant_id`, `user_id` and the optional section slug inside every store query. The scope is built by the application from authenticated identity, never from speech or model output.
- Ingests approved UTF-8 text/Markdown as heading-aware chunks, keeps immutable versions and returns only the current approved version. Search is PostgreSQL FTS plus a deterministic Python re-ranker (no embeddings).
- Applies document authorization inside SQL: tenant, owner or same-tenant read grant, `approved=true`, section (`NULL` = user-wide), not deleted and current version. The owner may approve, share, revoke, delete or purge; a reader cannot manage.
- Exposes `search_knowledge` only when `TARA_KNOWLEDGE_ENABLED=1` and memory is enabled. An empty result and a failed lookup are distinct; the instruction requires a citation and forbids guessing.
- Keeps extraction, consolidation, summaries, recovery and document queries off the audio path. Startup work and tool calls are bounded by `TARA_MEMORY_*_TIMEOUT_S` and `TARA_KNOWLEDGE_TIMEOUT_S`; a failure returns no context or a tool error instead of blocking voice.

## What it does not do

- It does not embed text or use pgvector. Paraphrases with no shared words can still be missed; memory and documents always keep a keyword path.
- It does not ingest PDF, Word, HTML, spreadsheets, images or URLs. Document RAG reads UTF-8 `.txt`, `.md` and `.markdown` only; there is no crawler or OCR.
- It does not do automatic per-turn document retrieval or a full Live context assembler. The model must call `search_knowledge`.
- It does not use Redis. The app is single-process and has no measured need for a second store.
- It does not implement document write/admin/group ACLs, PostgreSQL Row-Level Security, audit logs or encryption at rest. Document read ACLs are application-enforced and owner-managed.
- It does not provide authentication. The local demo hardcodes a local tenant and user; a real deployment must build `MemoryScope` from authenticated identity.
- It does not prove that the real Gemini model calls the tools correctly or cites documents. Only fake-backend tests have run.
- It does not claim measured latency or retrieval quality. The ranking weights are starting values in `src/tara/memory/retrieval.py` and `src/tara/memory/knowledge.py`.

## Prerequisites

- PostgreSQL running locally (tests ran against PostgreSQL 16).
- The optional Python extra: `.venv/bin/python -m pip install -e '.[memory]'` (installs `psycopg[binary,pool]`).
- A dedicated database. Do not point `TARA_MEMORY_DATABASE_URL` at an unrelated application's database.

## Setup

```bash
# 1. create a dedicated database (owner is the local OS user)
createdb tara_memory

# 2. install the optional driver
.venv/bin/python -m pip install -e '.[memory]'

# 3. create/update the schema (idempotent; applies 001..006)
TARA_MEMORY_DATABASE_URL=postgresql:///tara_memory .venv/bin/python -m tara.memory.migrate
```

Migration `005_recovery_and_ranking.sql` adds `meeting_sessions.interrupted`, `owner_pid`, `owner_host` and the indexes used by filtered retrieval. Migration `006_knowledge.sql` adds the document tables, full-text index and read ACLs. Both preserve existing rows.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TARA_MEMORY_ENABLED` | `0` | Turns memory on. Off means no tools and no startup query. |
| `TARA_MEMORY_DATABASE_URL` | empty | `postgresql://...` or `postgres://...`. Required when enabled. Never logged or included in `repr`. |
| `TARA_MEMORY_SCHEMA` | empty (public) | Optional schema; the integration tests use a throwaway schema. |
| `TARA_MEMORY_SEARCH_LIMIT` | `5` | Startup block candidate count and default recall limit. |
| `TARA_MEMORY_CONTEXT_CHARS` | `3500` | Character cap for the startup block; not a model token limit. |
| `TARA_MEMORY_STARTUP_CONTEXT` | `1` | Inject recent memories when a Live session opens. |
| `TARA_MEMORY_WRITE_TIMEOUT_S` | `3` | Bound on a memory write. |
| `TARA_MEMORY_RECALL_TIMEOUT_S` | `2` | Bound on recall/startup retrieval. |
| `TARA_KNOWLEDGE_ENABLED` | `0` | Registers `search_knowledge`; requires memory enabled and the same PostgreSQL URL. |
| `TARA_KNOWLEDGE_SEARCH_LIMIT` | `4` | Passages returned per document search (1–10). |
| `TARA_KNOWLEDGE_CONTEXT_CHARS` | `2500` | Character cap for one document tool result (200–20,000). |
| `TARA_KNOWLEDGE_TIMEOUT_S` | `2` | Bound on a document search. |
| `TARA_EPISODE_RETENTION_DAYS` | `90` | Transcript chunks. |
| `TARA_FACT_RETENTION_DAYS` | `180` | Unconfirmed extracted facts. |

`RuntimeSettings.redacted()` reports only `database_url: set|missing`; the URL is a secret-bearing value and is not logged. Knowledge settings are shown without the URL.

## Data model

Implemented tables (full details in [MEMORY_DATA_MODEL](MEMORY_DATA_MODEL.md)):

- `memory_records`: `id`, `tenant_id`, `user_id`, `session_id`, `meeting_id`, `kind`, `body`, `status`, `source`, `created_at`, `updated_at`, `expires_at`, `confidence`, `supersedes_id`, `metadata`, `deleted_at`, generated `search`.
- `meeting_sessions`: numbered meetings inside a section, `started_at`, `ended_at`, `interrupted`, `owner_pid`, `owner_host`.
- `knowledge_sources`: document family, owner, optional section, `title`, `source`, `approved`, `current_version`, `content_hash`, `effective_date`, `deleted_at`.
- `document_versions`: immutable `version`, `content_hash`, `status`, `effective_date`.
- `document_chunks`: `ordinal`, `heading_path`, `text`, generated `search` tsvector, foreign keys to source and version.
- `acl_grants`: same-tenant `subject_type='user'`, `subject_id`, `resource_type='document'`, `resource_id`, `permission='read'`.
- `schema_migrations`.

Canonical persisted kinds: `fact`, `preference`, `project`, `decision`, `correction`, `note`, `episode`, `summary`, `action`. Live note kinds (`decision`, `action_item`, `open_question`, `proposal`, `summary`) are translated once in `src/tara/memory/kinds.py`; `action_item` is accepted from older code, model output and tools but is always stored as `action`.

`status` is only `confirmed`, `candidate` or `superseded`. Confidence, confirmation, tentative wording, conflict flags and duplicate links are separate dimensions in `metadata`; one field is never overloaded for another meaning.

## Retrieval

1. `MemoryService.recall` parses the spoken query into text plus optional `since`/`until` and kind filters (`retrieval.parse_query`).
2. The store searches only inside the bound scope, excludes superseded and expired rows, and applies the kind/time filters in SQL (PostgreSQL) or the equivalent predicate (in-memory).
3. `retrieval.rank_hits` re-ranks candidates: FTS rank, rarity-weighted keyword coverage, phrase match, status, kind, recency (45-day half-life), conflict penalty and a diversity pass that removes near-duplicates.
4. If a time or kind filter produced no keyword candidates, the newest records inside that window are used instead, labelled as `recent` rather than an exact match.
5. `recall_memory` returns id, text, kind, source, status, label, meeting number/time, and a conflict warning. The model is told to say plainly when nothing was found.

Cross-meeting recall happens inside one section (scope `meeting_id` is the section). Records with no section are global for that user. Another user or tenant never matches, even with identical text.

## Document RAG operations

Use `tara knowledge` against the same database; run `tara migrate` first. The CLI path defaults to `TARA_MEMORY_DATABASE_URL`, tenant `local` (or `TARA_MEMORY_TENANT`) and user `local-user` (or `TARA_MEMORY_USER`). It never prints the URL.

```bash
tara knowledge add ./handbook.md                  # approved by default
tara knowledge add ./pricing.md --draft           # invisible until approved
tara knowledge add ./onboarding.md --section onboarding
tara knowledge list
tara knowledge search "refund window"
tara knowledge approve <document-id>
tara knowledge share <document-id> alice
tara knowledge revoke <document-id> alice
tara knowledge delete <document-id>               # soft delete
tara knowledge delete <document-id> --hard        # cascade versions, chunks, ACLs
tara knowledge purge --yes
```

Ingestion accepts UTF-8 `.txt`, `.md` and `.markdown`; other formats are refused. Chunking is heading-aware (1,200 characters, 150-character overlap, 2,000 chunks maximum). Identity is `(tenant, owner, section, source)`; identical content is idempotent, changed content creates a new immutable version, and reverting reuses an older version. Search sees only `approved=true`, the current version, the owner or a same-tenant read grant, and the caller's section (`NULL` section is user-wide). Soft delete hides immediately; hard delete and `purge_deleted` remove all associated rows. Full contract: [DOCUMENT_RAG](DOCUMENT_RAG.md).

`search_knowledge` returns `count`, passages with title, version, effective date, source and citation, or a `count=0` note. A store failure is a tool error, not an empty result. The tool is registered only when `TARA_KNOWLEDGE_ENABLED=1` and memory is enabled; an injected service is used by tests.

## Consolidation, corrections and promotion

- An exact duplicate save is idempotent. A repeated automatic candidate is reinforced (`metadata.sightings`), not duplicated.
- A candidate the recorder sees again in a later meeting is promoted to confirmed and stops expiring, unless it is marked tentative (a repeated proposal is still not agreement).
- `consolidate(scope)` merges only near-identical durable records; it is bounded to the newest 200 and runs once at session start in a background task.
- An explicit correction resolves its target from the correction text. If two records are nearly equally plausible, both are preserved and the correction is flagged. A model-proposed `corrects` reference is resolved the same way; a reference to a record Tara does not hold is ignored.
- Superseding never deletes: the old row keeps its provenance and `status=superseded`, and `get` can still read it for historical questions.

## Startup, meetings and recovery

On start: `start_session` assigns the next meeting number, `recover` closes abandoned meetings (interrupted, end time = last activity), expired rows are purged, and consolidation runs in the background. `meeting_brief` tells the model which meeting this is and lists earlier meetings with summaries and anything they left open. `startup_context` then adds the bounded memory block. Neither call is allowed to stop the voice session.

A process exit calls `end_session` so a normal meeting is marked completed (`interrupted=false`). Recovery only touches rows with no end time. Because owner pid/host are recorded, a second live process on the same host is not closed by mistake. A row from another host cannot be liveness-checked; the single-process deployment assumption is documented in ADR-0016.

## Observability

Counters include: `memory_writes`, `memory_deduplicated`, `memory_conflicts_flagged`, `memory_corrections_linked`, `memory_consolidated`, `memory_recovered_sessions`, `memory_recalls`, `memory_recall_filtered`, `memory_forgets`, `memory_forget_misses`, `memory_write_failures`, `memory_recall_failures`, `memory_forget_failures`, `memory_startup_failures`, `memory_consolidate_failures`, `memory_recovery_failures`, `memory_session_start_failures`. Gauges: `memory_recall_results`. Latency observations: `memory_write_ms`, `memory_recall_ms`, `memory_forget_ms`, `memory_startup_ms`.

Document counters: `knowledge_documents_ingested`, `knowledge_chunks_ingested`, `knowledge_searches`, `knowledge_search_failures`, `knowledge_ingest_failures`, `knowledge_documents_deleted`, `knowledge_documents_purged`, `knowledge_documents_approved`, `knowledge_documents_unapproved`, `knowledge_documents_shared`, `knowledge_documents_unshared`. Latency: `knowledge_ingest_ms`, `knowledge_search_ms`.

No memory text, query or URL is logged. `--debug` prints only counters and event metadata.

## Measured results (2026-10-23)

Commands run in this workspace:

```text
.venv/bin/python -m pytest -q
718 passed, 10 skipped, 7 deselected in 85.23s

TARA_TEST_DATABASE_URL=postgresql:///postgres \
  .venv/bin/python -m pytest -m db tests/memory/test_memory_postgres.py -q
10 passed in 0.35s

.venv/bin/ruff check src tests
All checks passed!

.venv/bin/mypy src
Success: no issues found in 140 source files
```

These are suite timings, **not** a voice latency benchmark. No first-audio measurement was taken with memory enabled. PostgreSQL tests use a throwaway schema and a real local server; they cover filtered retrieval, implicit correction, recovery state and a two-store restart with a correction.

## Failure modes

| Failure | Behaviour |
|---|---|
| PostgreSQL down at session start | Startup block is empty; `memory_startup_failures` increments; voice continues. |
| PostgreSQL down during a save | Tool returns an error; the model is instructed not to claim the save succeeded. |
| PostgreSQL down during recall | Tool returns an error; the model must say it cannot recall, not invent. |
| Timeout | Bounded by `TARA_MEMORY_*_TIMEOUT_S`; reported as a tool error or empty startup context. |
| Memory superseded | Old row remains with `status=superseded` and its provenance; retrieval excludes it, `get` still reads it. |
| Ambiguous correction | No record is superseded; both candidates and the correction are flagged `possible_conflict`. |
| Wrong tenant/user/section | Query returns nothing; canary tests cover another user, tenant, section and a revoked document grant. |
| Document not approved or deleted | Search excludes it; `get` returns nothing to a reader. |
| Document store unavailable | `search_knowledge` returns a tool error; the model must say the lookup failed, not that no document exists. |
| Unsupported document format | CLI refuses it; no partial ingestion. |
| Driver missing | `PostgresMemoryStore`/`PostgresKnowledgeStore` raises `ConfigError` at construction with the install command. |

## Tests

- Offline quality: `tests/memory/test_memory_retrieval.py` covers kind translation, proposal vs decision, digest validation, durable session notes, correction linking and ambiguity, consolidation, numbered-record safety, temporal filters, ranking, startup labels, recovery and tool output.
- Core: `tests/memory/test_memory_core.py` covers service rules, isolation, dedupe, expiry, rendering, intent and failure reporting.
- Engine: `tests/memory/test_memory_engine.py` covers the transcript gate, tool results and startup injection with the fake provider.
- Documents offline: `tests/memory/test_knowledge.py` covers chunking, approval, tenant/user/section isolation, read grants, draft sharing, versions/reverts, idempotency, soft/hard deletion, formats, context budgets, the live tool and failure honesty.
- Live wiring: `tests/voice/test_voice_app_settings.py` checks the `search_knowledge` grant, instruction and health field; `tests/tools/test_catalog.py` keeps the catalogue in step with the source `ToolDef`.
- Real PostgreSQL: `tests/memory/test_memory_postgres.py` and `tests/memory/test_knowledge_postgres.py` create throwaway schemas, migrate them, and verify commit, fresh-store reads, isolation, filters, correction/recovery, document ACL/versions/purge and failure honesty. They are skipped unless `TARA_TEST_DATABASE_URL` is set (marker `db`).

## Remaining work

1. Verify real Gemini Live tool calling for save/recall/forget/correction and `search_knowledge` with a human listening test, including citation behaviour.
2. Evaluate memory and document retrieval quality with a labelled question set; only then tune ranking weights or justify embeddings/pgvector.
3. Add document admin/write/group ACLs, RLS and an audit log before multi-tenant use.
4. Measure startup, recall and document-search latency with memory/knowledge enabled on the target machine.
