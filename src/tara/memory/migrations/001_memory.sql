-- Tara persistent memory, v1. Idempotent: safe to run on every deploy.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_records (
    id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    session_id text NOT NULL DEFAULT '',
    meeting_id text,
    kind text NOT NULL CHECK (kind IN ('fact', 'preference', 'project', 'decision', 'correction', 'note')),
    body text NOT NULL CHECK (length(btrim(body)) > 0),
    status text NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'candidate', 'superseded')),
    source text NOT NULL CHECK (length(btrim(source)) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    confidence real NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    supersedes_id uuid REFERENCES memory_records(id) ON DELETE SET NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    deleted_at timestamptz,
    search tsvector GENERATED ALWAYS AS (to_tsvector('english', body)) STORED
);

CREATE INDEX IF NOT EXISTS memory_scope_recent_idx
    ON memory_records (tenant_id, user_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS memory_search_idx ON memory_records USING GIN (search);
CREATE INDEX IF NOT EXISTS memory_session_idx ON memory_records (tenant_id, user_id, session_id);

INSERT INTO schema_migrations (version) VALUES ('001_memory') ON CONFLICT (version) DO NOTHING;
