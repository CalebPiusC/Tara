# Memory Data Model (PostgreSQL)

**Status: Partially implemented (2026-10-23).** Implemented tables: `memory_records`, `meeting_sessions`, `knowledge_sources`, `document_versions`, `document_chunks`, `acl_grants` and `schema_migrations` (migrations 001–006). Everything else in this document is proposed and has no migration. Every tenant-owned table is filtered by `tenant_id`, and memory/document reads also filter `user_id`/owner or a read grant. See [MEMORY_OPERATIONS](MEMORY_OPERATIONS.md), [DOCUMENT_RAG](DOCUMENT_RAG.md), [ADR-0016](../adr/0016-memory-retrieval-consolidation-recovery.md) and [ADR-0017](../adr/0017-document-rag.md).

## Implemented today

| Table | Columns that matter | Notes |
|---|---|---|
| `memory_records` | `id`, `tenant_id`, `user_id`, `session_id`, `meeting_id`, `kind`, `body`, `status`, `source`, `created_at`, `updated_at`, `expires_at`, `confidence`, `supersedes_id`, `metadata` (jsonb), `deleted_at`, generated `search` | Kinds: `fact`, `preference`, `project`, `decision`, `correction`, `note`, `episode`, `summary`, `action`. Status: `confirmed`, `candidate`, `superseded`. Expiry is enforced on read as well as by purge. |
| `meeting_sessions` | `tenant_id`, `user_id`, `section_id`, `number`, `session_id`, `started_at`, `ended_at`, `interrupted`, `owner_pid`, `owner_host`, `deleted_at` | A section is the scope's `meeting_id`; each run is a numbered meeting. `ended_at IS NULL` means unfinished; `interrupted` means recovery closed it, not that it completed normally. |
| `knowledge_sources` | `id`, `tenant_id`, `owner_user_id`, `section_id`, `title`, `source`, `approved`, `current_version`, `content_hash`, `effective_date`, `created_at`, `updated_at`, `deleted_at` | One document family. Identity is `(tenant, owner, coalesce(section,''), source)` among live rows. `section_id NULL` is user-wide. Soft delete hides immediately. |
| `document_versions` | `id`, `tenant_id`, `source_id`, `version`, `content_hash`, `status`, `effective_date`, `created_at` | Immutable versions. `UNIQUE (source_id, version)` and `UNIQUE (source_id, content_hash)`; only `status='indexed'` at `current_version` is searchable. Reverting reuses an old row. |
| `document_chunks` | `id`, `tenant_id`, `source_id`, `version_id`, `ordinal`, `heading_path`, `text`, generated `search` | Heading-aware chunks with a GIN full-text index. No embedding column. Cascades from the source/version. |
| `acl_grants` | `id`, `tenant_id`, `subject_type`, `subject_id`, `resource_type`, `resource_id`, `permission`, `created_at` | Same-tenant read grants for documents (`subject_type='user'`, `resource_type='document'`, `permission='read'`). Owner access is implicit. |
| `schema_migrations` | `version`, `applied_at` | Applied idempotently by `tara.memory.migrate`. |

Indexes include live-row partial indexes by `(tenant_id, user_id, updated_at)`, `kind` and `status`, a session timeline index, GIN on `memory_records.search`, document identity and owner/section indexes, GIN on `document_chunks.search`, and the ACL subject index. Migration 005 adds the recovery columns and retrieval indexes; migration 006 adds the document tables.

## Proposed (not built)

## Needed in v1
| Table | Purpose | PK | Key relations | Retention |
|---|---|---|---|---|
| `organizations` | Tenant boundary | `id` | parent of everything | until org deletion (cascades) |
| `users` | Principals who own sources or organize meetings | `id` | `tenant_id` | org lifetime; erasable on request |
| `meetings` | One meeting, its config version, retention | `id` | `tenant_id`, `organizer_id` | per `retention_days` |
| `meeting_participants` | Participants seen in a meeting with role | `(meeting_id, participant_id)` | `meetings` | with meeting |
| `conversation_events` | Final transcript events with attribution | `id` | `meeting_id` | with meeting; text optionally redacted earlier |
| `memory_records` | Decisions, actions, open questions, summaries, statements | `id` | `meeting_id`, provenance | per kind (see below) |
| `indexing_jobs` | Idempotent ingestion/embedding work (not needed by the synchronous path today) | `id` | `source_id`, `version_id` | short |
| `audit_log` | Append-only access and mutation record (no content) | `id` | `tenant_id` | long, config |

## Deferred (and why)
`projects` and `teams` (use `acl_grants` with a `group` subject and a `scope_label` on sources until real need), `user_preferences` and recurring-participant memory (privacy design needed first), separate `embeddings` table (embedding lives on the chunk with `embedding_model`; split only when multiple models coexist), `meeting_summaries` and `decisions`/`action_items` as tables (they are `memory_records.kind` values until query patterns demand otherwise), retrieval result cache (see security doc; start with no persistent cache).

## DDL sketch (target, illustrative; not the implemented schema)

> **Read this first:** the SQL below is the original target sketch for organizations, meetings, events and a future vector leg. It is **not** what runs. The implemented schema is migrations `001`–`006`; in particular the implemented document tables have **no `embedding` column and no pgvector extension**, and their exact columns are in the table above and `006_knowledge.sql`. Use the migration as the authority.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE organizations (id uuid PRIMARY KEY, name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE meetings (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  organizer_id uuid, config_version text NOT NULL, mode text NOT NULL,
  started_at timestamptz, ended_at timestamptz, retention_days int NOT NULL CHECK (retention_days >= 0));

CREATE TABLE conversation_events (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, meeting_id uuid NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  seq bigint NOT NULL, ts_ms bigint NOT NULL,
  participant_id text,                    -- NULL = unknown speaker
  attribution_confidence real, overlap boolean NOT NULL DEFAULT false, mapping_version int,
  text text, UNIQUE (meeting_id, seq));

CREATE TABLE knowledge_sources (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  title text NOT NULL, kind text NOT NULL, approved boolean NOT NULL DEFAULT false,
  scope_label text, current_version_id uuid, deleted_at timestamptz);

CREATE TABLE document_versions (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, source_id uuid NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
  content_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  effective_date date, status text NOT NULL CHECK (status IN ('pending','indexed','failed','superseded','deleted')),
  UNIQUE (source_id, content_hash));      -- duplicate ingestion is a no-op

CREATE TABLE document_chunks (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, source_id uuid NOT NULL, version_id uuid NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
  ordinal int NOT NULL, heading_path text, text text NOT NULL,
  embedding_model text NOT NULL, embedding vector(384),   -- dim follows the chosen model
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(heading_path,'') || ' ' || text)) STORED,
  UNIQUE (version_id, ordinal));
CREATE INDEX ON document_chunks USING GIN (tsv);
CREATE INDEX ON document_chunks (tenant_id, source_id);
-- vector index (HNSW) is added only if exact search misses the latency budget (see evaluation).

CREATE TABLE memory_records (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, meeting_id uuid REFERENCES meetings(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('decision','action_item','open_question','summary','statement','fact')),
  status text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','confirmed','disputed','superseded','rejected')),
  body text NOT NULL, subject_participant_id text, attribution_confidence real,
  source_event_ids uuid[] NOT NULL DEFAULT '{}', source_chunk_ids uuid[] NOT NULL DEFAULT '{}',
  supersedes uuid REFERENCES memory_records(id), expires_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (cardinality(source_event_ids) + cardinality(source_chunk_ids) > 0));   -- no provenance, no memory

CREATE TABLE acl_grants (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, subject_type text NOT NULL, subject_id text NOT NULL,
  resource_type text NOT NULL, resource_id uuid NOT NULL, permission text NOT NULL DEFAULT 'read');

CREATE TABLE indexing_jobs (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, version_id uuid NOT NULL, state text NOT NULL,
  attempts int NOT NULL DEFAULT 0, last_error text, UNIQUE (version_id));

CREATE TABLE audit_log (
  id bigserial PRIMARY KEY, tenant_id uuid NOT NULL, ts timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL, action text NOT NULL, resource_type text, resource_ids uuid[], meta jsonb);  -- ids and counts only, never content
```
`users` and `meeting_participants` follow the same pattern and are omitted from the sketch. Row-Level Security policies keyed on `tenant_id` are proposed as defense in depth; Postgres RLS docs have not been read for this phase (Q-R6).

## Retrieval query shape

**Implemented today:** one PostgreSQL FTS leg (no vector leg, no RRF). The SQL in `PostgresKnowledgeStore.search` filters `s.tenant_id`, `s.approved`, `s.deleted_at IS NULL`, the section rule, the owner/read-grant subquery, `v.status='indexed' AND v.version=s.current_version`, then ranks with `ts_rank_cd`; `KnowledgeService.rank_hits` re-ranks in Python. The sketch below is the future two-leg target.

```sql
-- target only: both legs run with the same WHERE; fused in application code (RRF)
SELECT c.id, ts_rank_cd(c.tsv, q, 1|32) AS rank
FROM document_chunks c
JOIN document_versions v ON v.id = c.version_id AND v.status = 'indexed'
JOIN knowledge_sources s ON s.id = c.source_id AND s.deleted_at IS NULL AND s.approved
JOIN current_version cv ON ...   -- only the source's current version
, websearch_to_tsquery('english', $query) q
WHERE c.tenant_id = $tenant AND s.id = ANY($allowed_sources) AND c.tsv @@ q
ORDER BY rank DESC LIMIT 20;
-- vector leg: same WHERE, ORDER BY c.embedding <=> $qvec LIMIT 20  (exact scan for small tenants)
```
Pgvector caveat: with an approximate index the filter applies after the index scan and can starve results (R1); hence exact search first and tenant-scoped partial indexes later.

## Updates, invalidation, deletion (implemented)
- **Update:** new content hash creates a new `document_versions` row and its chunks in one transaction; the source's `current_version` moves and old versions are marked `superseded`. Identical content is idempotent; reverting reuses the old immutable version. A failed write leaves the old version current. There is no background sweeper: old versions remain until the source is hard-deleted.
- **Deletion of a source:** soft delete sets `deleted_at` and retrieval excludes it immediately. Hard delete removes the source and cascades versions, chunks and ACL rows. `purge_deleted` does the same for all of the owner's soft-deleted documents. No memory records are derived from documents, so there is nothing to rewrite.
- **Embedding model change:** not applicable; there is no embedding model or vector column today.
- **Meeting deletion:** memory sessions and records are handled by the memory path; documents are owned independently and are not deleted with a meeting.
- **Personal data erasure:** not implemented at the user/organization level.
- **Audit:** not implemented; mutations are counted by metrics but not tied to a user or document id in a log.

## Open questions
Vector dimension must be fixed per column: changing models means a new column or table. Whether to partition `document_chunks` by tenant. Whether transcript text should be encrypted at the column level.
