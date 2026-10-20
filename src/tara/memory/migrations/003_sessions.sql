-- Tara memory v3: a section (chat) holds numbered meetings, each with start and end times. Idempotent.
CREATE TABLE IF NOT EXISTS meeting_sessions (
    tenant_id  text NOT NULL,
    user_id    text NOT NULL,
    section_id text NOT NULL,
    number     integer NOT NULL CHECK (number >= 1),
    session_id text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at   timestamptz,
    PRIMARY KEY (tenant_id, user_id, section_id, session_id),
    UNIQUE (tenant_id, user_id, section_id, number)
);

INSERT INTO schema_migrations (version) VALUES ('003_sessions') ON CONFLICT (version) DO NOTHING;
