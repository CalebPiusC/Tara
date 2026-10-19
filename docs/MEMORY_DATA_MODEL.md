# Memory Data Model (PostgreSQL)

**Status: Proposed. No migration exists.** Only entities needed for the first version are defined; the rest are deferred with reasons. Every tenant-owned table has `tenant_id` and is filtered by it in every query.

## Needed in v1
| Table | Purpose | PK | Key relations | Retention |
|---|---|---|---|---|
| `organizations` | Tenant boundary | `id` | parent of everything | until org deletion (cascades) |
| `users` | Principals who own sources or organize meetings | `id` | `tenant_id` | org lifetime; erasable on request |
| `meetings` | One meeting, its config version, retention | `id` | `tenant_id`, `organizer_id` | per `retention_days` |
| `meeting_participants` | Participants seen in a meeting with role | `(meeting_id, participant_id)` | `meetings` | with meeting |
| `conversation_events` | Final transcript events with attribution | `id` | `meeting_id` | with meeting; text optionally redacted earlier |
| `memory_records` | Decisions, actions, open questions, summaries, statements | `id` | `meeting_id`, provenance | per kind (see below) |
| `knowledge_sources` | A document family (a file, URL, wiki page) | `id` | `tenant_id` | until deleted |
| `document_versions` | Immutable version of a source with content hash | `id` | `source_id` | latest N kept; older tombstoned |
| `document_chunks` | Chunk text, embedding, tsvector, provenance | `id` | `version_id` | follows its version |
| `indexing_jobs` | Idempotent ingestion/embedding work | `id` | `source_id`, `version_id` | short |
| `acl_grants` | Who can read what | `id` | subject, resource | until revoked |
| `audit_log` | Append-only access and mutation record (no content) | `id` | `tenant_id` | long, config |

## Deferred (and why)
`projects` and `teams` (use `acl_grants` with a `group` subject and a `scope_label` on sources until real need), `user_preferences` and recurring-participant memory (privacy design needed first), separate `embeddings` table (embedding lives on the chunk with `embedding_model`; split only when multiple models coexist), `meeting_summaries` and `decisions`/`action_items` as tables (they are `memory_records.kind` values until query patterns demand otherwise), retrieval result cache (see security doc; start with no persistent cache).

## DDL sketch (illustrative; unrun, to be verified against a real Postgres in phase R1)
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
```sql
-- both legs run with the same WHERE; fused in application code (RRF)
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

## Updates, invalidation, deletion
- **Update:** new content hash creates a new `document_versions` row (`pending`). On successful indexing, one transaction sets `current_version_id`, marks the old version `superseded` and its chunks excluded. Failed indexing leaves the old version current. Old chunks are physically deleted by a sweeper after the grace period.
- **Embedding model change:** new chunks with new `embedding_model`; queries only compare vectors of the same model.
- **Deletion of a source:** set `deleted_at` (retrieval excludes immediately in the same transaction), enqueue hard delete of versions and chunks; `memory_records.source_chunk_ids` pointing at deleted chunks are flagged `source_removed`, and records that were derived only from that source are deleted; summaries that cited it are marked stale and regenerated or dropped; any retrieval cache entry keyed by the source or version is evicted.
- **Meeting deletion:** cascades events and records; summaries live in `memory_records` so they cascade too; exported reports are outside the database and are tracked in the audit log for manual purge.
- **Personal data erasure:** `users` erasure nulls identifying fields; `conversation_events.participant_id` stays as a pseudonymous id unless the request demands transcript removal.

## Open questions
Vector dimension must be fixed per column: changing models means a new column or table. Whether to partition `document_chunks` by tenant. Whether transcript text should be encrypted at the column level.
