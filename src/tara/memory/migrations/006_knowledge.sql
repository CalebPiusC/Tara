-- Tara document RAG v6: approved documents, immutable versions, chunks and per-document ACLs.
-- Idempotent: safe to run on every deploy. No embedding, vector or network crawler columns.
CREATE TABLE IF NOT EXISTS knowledge_sources (
    id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    owner_user_id text NOT NULL,
    section_id text,
    title text NOT NULL CHECK (length(btrim(title)) > 0),
    source text NOT NULL CHECK (length(btrim(source)) > 0),
    approved boolean NOT NULL DEFAULT true,
    current_version integer NOT NULL DEFAULT 0 CHECK (current_version >= 0),
    content_hash text NOT NULL DEFAULT '',
    effective_date date,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS knowledge_sources_identity_uq
    ON knowledge_sources (tenant_id, owner_user_id, coalesce(section_id, ''), source)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS knowledge_sources_owner_idx
    ON knowledge_sources (tenant_id, owner_user_id, updated_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS knowledge_sources_section_idx
    ON knowledge_sources (tenant_id, section_id, approved, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS document_versions (
    id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    source_id uuid NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version >= 1),
    content_hash text NOT NULL CHECK (length(content_hash) > 0),
    status text NOT NULL DEFAULT 'indexed'
        CHECK (status IN ('pending', 'indexed', 'failed', 'superseded')),
    effective_date date,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, version),
    UNIQUE (source_id, content_hash)
);
CREATE INDEX IF NOT EXISTS document_versions_source_idx
    ON document_versions (source_id, version DESC);

CREATE TABLE IF NOT EXISTS document_chunks (
    id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    source_id uuid NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
    version_id uuid NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    heading_path text NOT NULL DEFAULT '',
    text text NOT NULL CHECK (length(btrim(text)) > 0),
    search tsvector GENERATED ALWAYS AS
        (to_tsvector('english', coalesce(heading_path, '') || ' ' || text)) STORED,
    UNIQUE (version_id, ordinal)
);
CREATE INDEX IF NOT EXISTS document_chunks_search_idx ON document_chunks USING GIN (search);
CREATE INDEX IF NOT EXISTS document_chunks_version_idx ON document_chunks (tenant_id, source_id, version_id);

CREATE TABLE IF NOT EXISTS acl_grants (
    id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    subject_type text NOT NULL CHECK (subject_type IN ('user')),
    subject_id text NOT NULL CHECK (length(btrim(subject_id)) > 0),
    resource_type text NOT NULL CHECK (resource_type IN ('document')),
    resource_id uuid NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
    permission text NOT NULL DEFAULT 'read' CHECK (permission IN ('read')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (subject_type, subject_id, resource_type, resource_id, permission)
);
CREATE INDEX IF NOT EXISTS acl_grants_subject_idx
    ON acl_grants (tenant_id, subject_type, subject_id, resource_type, permission);

INSERT INTO schema_migrations (version) VALUES ('006_knowledge') ON CONFLICT (version) DO NOTHING;
