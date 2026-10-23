-- Tara memory v5: recovery state and indexes for filtered retrieval. Idempotent.
ALTER TABLE meeting_sessions ADD COLUMN IF NOT EXISTS interrupted boolean NOT NULL DEFAULT false;
ALTER TABLE meeting_sessions ADD COLUMN IF NOT EXISTS owner_pid integer NOT NULL DEFAULT 0;
ALTER TABLE meeting_sessions ADD COLUMN IF NOT EXISTS owner_host text NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS memory_kind_recent_idx
    ON memory_records (tenant_id, user_id, kind, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS memory_status_recent_idx
    ON memory_records (tenant_id, user_id, status, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS memory_session_timeline_idx
    ON memory_records (tenant_id, user_id, meeting_id, session_id, created_at) WHERE deleted_at IS NULL;

INSERT INTO schema_migrations (version) VALUES ('005_recovery_and_ranking') ON CONFLICT (version) DO NOTHING;
