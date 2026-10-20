-- Tara memory v2: meeting-scoped memory, transcript chunks (episodes), summaries. Idempotent.
ALTER TABLE memory_records DROP CONSTRAINT IF EXISTS memory_records_kind_check;
ALTER TABLE memory_records ADD CONSTRAINT memory_records_kind_check
    CHECK (kind IN ('fact', 'preference', 'project', 'decision', 'correction', 'note', 'episode', 'summary', 'action'));

CREATE INDEX IF NOT EXISTS memory_meeting_recent_idx
    ON memory_records (tenant_id, user_id, meeting_id, updated_at DESC) WHERE deleted_at IS NULL;

INSERT INTO schema_migrations (version) VALUES ('002_meetings') ON CONFLICT (version) DO NOTHING;
