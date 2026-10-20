-- Tara memory v4: a deleted meeting keeps its number (so numbers never repeat) but disappears from lists. Idempotent.
ALTER TABLE meeting_sessions ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

INSERT INTO schema_migrations (version) VALUES ('004_session_delete') ON CONFLICT (version) DO NOTHING;
