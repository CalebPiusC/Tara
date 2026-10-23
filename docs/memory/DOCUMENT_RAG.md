# Document RAG

**Status: Implemented and tested offline plus against a real local PostgreSQL on 2026-10-23. Not verified with real Gemini tool calling.** Approved documents are ingested, chunked, versioned, authorized, searched with PostgreSQL full-text search and a deterministic re-ranker, and deleted. There are no embeddings, no vector database, no PDF/Word parser and no crawler. Retrieved text is untrusted data. See [ADR-0017](../adr/0017-document-rag.md) and [MEMORY_AND_RAG](MEMORY_AND_RAG.md).

## What it is

Document RAG is separate from conversational memory:

- `src/tara/memory/` stores what was said and decided (`memory_records`).
- The document path stores approved reference material (`knowledge_sources`, `document_versions`, `document_chunks`, `acl_grants`) and answers `search_knowledge`.
- The live conversation state, floor, speaker identity and interruption decisions never read documents.

`src/tara/memory/knowledge.py` owns models, chunking, ranking, the `KnowledgeStore` Protocol and `KnowledgeService`. `knowledge_store.py` has the in-memory and PostgreSQL stores. `knowledge_tools.py` builds the Live tool. `knowledge_cli.py` is `tara knowledge`.

## Authorization model

Every read is bound to one application-built `MemoryScope(tenant_id, user_id, meeting_id)`. The model supplies only a query string; it cannot supply or widen a tenant, user, section, document id or ACL.

A document is readable only when all of these hold:

1. `tenant_id` matches the scope.
2. `deleted_at IS NULL` and `approved = true`.
3. The user is the owner, or has a same-tenant `acl_grants` row with `resource_type='document'` and `permission='read'`.
4. The section rule holds: a document with `section_id = NULL` is user-wide; a non-null `section_id` is visible only when the scope's section matches. A scope with no section sees both.

Owner rights are implicit: the owner may read, approve/unapprove, share, revoke and delete. There is no admin or write grant yet. A non-owner with read access who tries to manage a document gets `PermissionDenied`; a user with no access gets the same generic "not found" result, so the tool does not reveal that a document exists. Old versions are never returned: search joins the source's `current_version` and `status='indexed'`.

Documents default to approved when the owner explicitly ingests them through the CLI or service. `--draft` keeps one unapproved; search ignores it until `tara knowledge approve` (or `set_approved`) runs. Sharing a draft does not expose it: the reader still needs `approved=true`.

## Ingestion

Supported inputs are UTF-8 `.txt`, `.md` and `.markdown`. Other extensions are refused with a clear error rather than parsed optimistically. The service rejects non-UTF-8 bytes, empty text, documents over 2,000,000 characters and documents that would produce more than 2,000 chunks.

The document identity is `(tenant_id, owner_user_id, coalesce(section_id, ''), source)` among non-deleted rows. `source` defaults to the resolved file path, or an explicit stable name. Re-ingesting the same content is idempotent and updates title, approval and effective date. New content under the same identity becomes the next immutable version. Reverting to older content reuses that version's stored chunks rather than duplicating them. A different section or owner is a different document family.

The CLI is the operator path:

```bash
# migrate once; creates memory and document tables
TARA_MEMORY_DATABASE_URL=postgresql:///tara_memory .venv/bin/python -m tara.memory.migrate

# ingest a handbook for everyone; mark a draft; restrict one to a section
tara knowledge add ./handbook.md --tenant local --user local-user
tara knowledge add ./pricing.md --draft
tara knowledge add ./onboarding.md --section onboarding --effective-date 2026-10-01

tara knowledge list
tara knowledge list --drafts
tara knowledge search "refund window"
tara knowledge show <document-id>
tara knowledge approve <document-id>
tara knowledge approve <document-id> --unapprove
tara knowledge share <document-id> alice
tara knowledge revoke <document-id> alice
tara knowledge delete <document-id>              # soft delete
tara knowledge delete <document-id> --hard       # deletes now, cascades chunks and ACLs
tara knowledge purge --yes                       # permanently removes your soft-deleted documents
```

`--database-url`, `--schema`, `--tenant` and `--user` are accepted by every subcommand and default to `TARA_MEMORY_DATABASE_URL`, `TARA_MEMORY_SCHEMA` (empty/public), `TARA_MEMORY_TENANT` (`local`) and `TARA_MEMORY_USER` (`local-user`). `tara knowledge` shares the memory database; it never prints the URL.

## Chunking and versions

- Heading-aware: Markdown `#` headings become a `heading_path` such as `Refunds > Gift cards`.
- Paragraph packing with sentence splitting for a long paragraph, word-boundary hard splits for a sentence that is still too long, and a 150-character overlap.
- Default maximum is 1,200 characters per chunk, 2,000 chunks per document.
- Chunk boundaries are deterministic, so identical text produces identical chunks.
- `document_versions` rows are immutable. A new version is written with its chunks in one transaction, then the old version is marked `superseded` and the source's `current_version` moves. A failed transaction leaves the old version current.
- Soft delete sets `knowledge_sources.deleted_at` and excludes the family from search at once. Hard delete removes the source and cascades versions, chunks and ACL rows. `purge_deleted` does the same for all of the caller's soft-deleted documents.

Migration `006_knowledge.sql` creates the four tables, a generated `tsvector` on `heading_path + text`, a GIN index, and partial indexes for live documents. It is idempotent.

## Retrieval

1. `KnowledgeService.search` validates the query (non-empty, at most 300 characters) and asks the store for candidates inside the bound scope.
2. PostgreSQL runs `websearch_to_tsquery('english', ...)`, also matching the title and an `ILIKE` fallback. If a multi-word query has no rows, it retries with an OR query of content tokens. The SQL applies tenant, owner/ACL, approved, section, not-deleted and current-version filters before selecting text.
3. `rank_hits` re-ranks candidates deterministically: rarity-weighted keyword coverage, exact phrase, title, heading, and a small same-section boost. It chooses one passage per document first so one long document cannot crowd out every other source, then allows a second passage per document.
4. It respects `max_chars` and returns at most the requested limit (default 4). A citation carries title, version, effective date and source.

This is full-text search, not semantic search. A paraphrase with no shared content words can be missed; that limitation is deliberate until a labelled evaluation justifies embeddings or pgvector.

## Live wiring

When `TARA_MEMORY_ENABLED=1` and `TARA_KNOWLEDGE_ENABLED=1`, `LiveTaraApp` creates a `PostgresKnowledgeStore` on the memory database and registers `search_knowledge` with a code-owned `knowledge` grant. An injected `KnowledgeService` is also accepted (used by tests). The tool declaration is read-only and never granted from model text.

The instruction tells the model to call it for policies, pricing, product details and uploaded reference material, to cite title and version, and to say plainly when a result is empty or the lookup failed. An empty result returns `{"count": 0, "passages": [], "note": ...}`; the note forbids answering from general knowledge. A store failure propagates as a tool error, so the model cannot disguise a database failure as "no documents".

Settings:

| Variable | Default | Meaning |
|---|---|---|
| `TARA_KNOWLEDGE_ENABLED` | `0` | Registers the document tool. Requires `TARA_MEMORY_ENABLED=1` and the same PostgreSQL URL. |
| `TARA_KNOWLEDGE_SEARCH_LIMIT` | `4` | Passages per search (1–10). |
| `TARA_KNOWLEDGE_CONTEXT_CHARS` | `2500` | Character budget for the passages returned in one tool result (200–20,000). |
| `TARA_KNOWLEDGE_TIMEOUT_S` | `2.0` | Search timeout; ingestion uses the memory write timeout. |
| `TARA_MEMORY_SCHEMA` | public | Optional schema, shared with memory. |

`LiveTaraApp.health()` reports `knowledge: enabled|disabled`. The tool is not exposed when the service is absent. No document query runs per audio chunk; only an explicit model tool call touches the store.

## Metrics and tests

Counters: `knowledge_documents_ingested`, `knowledge_chunks_ingested`, `knowledge_searches`, `knowledge_search_failures`, `knowledge_ingest_failures`, `knowledge_documents_deleted`, `knowledge_documents_purged`, `knowledge_documents_approved`, `knowledge_documents_unapproved`, `knowledge_documents_shared`, `knowledge_documents_unshared`. Latencies: `knowledge_ingest_ms`, `knowledge_search_ms`. No document text, query or URL is logged.

Offline tests in `tests/memory/test_knowledge.py` cover chunk boundaries, approval, tenant/user/section isolation, sharing and revocation, draft sharing, versioning/reverts, idempotent ingestion, soft/hard deletion, file formats, context budgets, the live tool and failure reporting. `tests/memory/test_knowledge_postgres.py` repeats the core paths against a real PostgreSQL schema, including a fresh store reading a previously ingested document, ACL revocation, versions and purge. The app-wiring test is in `tests/voice/test_voice_app_settings.py`.

## What is not implemented

- Embeddings, pgvector, vector search, reciprocal-rank fusion or a semantic fallback.
- PDF, Word, HTML, spreadsheet, image or OCR ingestion; a crawler; a URL fetcher.
- Automatic retrieval on every turn or a full context assembler in the Live path.
- Admin/write/group ACLs, PostgreSQL Row-Level Security, an audit log, encryption at rest, or per-document retention policies.
- Real Gemini model behaviour: the tool, grant and instruction are wired and fake-backend tested, but whether the real model calls `search_knowledge` reliably and cites it correctly has not been verified with a human listening test.
- Retrieval quality or latency measurements. The ranking weights are starting values, not tuned against a labelled question set.
