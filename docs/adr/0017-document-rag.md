# ADR-0017: Document RAG with approved documents, versions and read ACLs

**Status: Accepted. Implemented offline and against a real local PostgreSQL on 2026-10-23; real Gemini tool calling is unverified.** Supersedes the document-RAG deferral in ADR-0016. Related: [DOCUMENT_RAG](../memory/DOCUMENT_RAG.md), [MEMORY_AND_RAG](../memory/MEMORY_AND_RAG.md), [ADR-0016](0016-memory-retrieval-consolidation-recovery.md).

## Context

Tara could persist conversational memory but had no document retrieval. The only `KnowledgeProvider` was an in-memory keyword stand-in with no ingestion, chunk store, versioning, ACLs or deletion path. That left the product unable to answer questions from an approved handbook, policy or product note, and it made "search_knowledge" a misleading tool.

The existing constraints still apply: one process, PostgreSQL as the only required service, no embeddings or vector database without measured evidence, authorization inside the query, provenance preserved, and retrieved text treated as untrusted data. Retrieval must stay off the audio path.

## Decision

1. **Add a separate document store.** `KnowledgeService`/`KnowledgeStore` in `src/tara/memory/` owns documents. It is not the query-only `KnowledgeProvider`, which remains only for old tests and dormant paths. The live session uses the scoped document service.
2. **Store four tables.** Migration `006_knowledge.sql` adds `knowledge_sources` (family and current version pointer), `document_versions` (immutable content hashes and effective dates), `document_chunks` (heading path, text, generated `tsvector`, provenance), and `acl_grants` (`resource_type='document'`, `permission='read'`). No embedding column.
3. **Scope from code, never from text.** `MemoryScope(tenant_id, user_id, meeting_id)` is built by the application. Every query filters tenant, owner/ACL, approval, section, not-deleted and current version before returning chunk text. The model supplies only a query. The owner may manage; another user needs a same-tenant read grant. Section `NULL` is user-wide; a non-null section is visible only in that section.
4. **Approved only.** Search returns `approved=true` current-version documents. `--draft` ingestion and `approve/unapprove` support review. Sharing a draft does not expose it.
5. **Deterministic ingestion.** UTF-8 `.txt`, `.md` and `.markdown` only. Heading-aware chunks, 1,200 characters, 150-character overlap, 2,000 chunks maximum. SHA-256 content hash; same hash is idempotent, new content is a new immutable version, reverting reuses an old version. Soft delete hides immediately; hard delete cascades versions, chunks and grants; `purge_deleted` completes the deletion path.
6. **Full-text retrieval plus Python re-ranking.** PostgreSQL `websearch_to_tsquery('english', ...)`, title and `ILIKE` fallbacks, an OR-token retry, then deterministic rarity/phrase/title/heading ranking with one passage per document first and a bounded context. No embeddings, vector store, Redis or crawler.
7. **Explicit live wiring.** `TARA_KNOWLEDGE_ENABLED=1` (with memory enabled and the same PostgreSQL URL) registers the read-only `search_knowledge` tool. Empty results are distinct from failures. The instruction requires citation and forbids answering from general knowledge. Health reports whether knowledge is enabled.
8. **CLI as the operator path.** `tara knowledge add|list|search|show|approve|share|revoke|delete|purge` uses the memory database and never prints the URL.

## Consequences

- Tara can answer from approved, versioned, authorized documents and show title, version, date and source.
- Tenant, user and section leaks are testable with canaries and real SQL. There is still no RLS or audit log, so this is application-enforced isolation, not a hardened multi-tenant deployment.
- A paraphrase with no shared content words can be missed. That is accepted until a labelled evaluation shows that embeddings or pgvector are worth the extra service and data exposure.
- The approved-only rule adds an operator step for drafts. The default CLI ingest is approved because the operator explicitly named the file; `--draft` is available for review workflows.
- Old versions remain stored until a hard delete of the source. There is no background version sweeper, and derived memories are not rewritten when a document changes.
- Real Gemini behaviour is unverified. The tool, grant and instruction are fake-backend tested; a human listening test is still needed for reliable calling and citation.

## Alternatives considered

- **Extend `KnowledgeProvider` in place.** Rejected: its query-only signature has no scope or ingestion path, and old tests and dormant code rely on it. A separate service keeps the boundary clear.
- **Add pgvector and embeddings now.** Rejected for this step: no labelled quality problem has been measured, it adds a required extension and data exposure, and the existing memory store already uses FTS plus a Python re-ranker.
- **Parse PDFs and Word files.** Rejected for now: no dependency or parser has been justified, and silently extracting a bad PDF would be worse than refusing it.
- **Default to unapproved drafts.** Kept as an option but not the default: the CLI operator explicitly selected the file, and forcing an approval step for every file would make the feature hard to use. The service and storage still enforce approved-only retrieval.
- **Admin/write/group ACLs and RLS.** Deferred: the read/share/revoke path is the immediate need; RLS and an audit log are separate security work.

## Verification

- `tests/memory/test_knowledge.py` (20 offline tests): chunking, provenance, approval, tenant/user/section isolation, sharing, draft sharing, versions/reverts, idempotency, deletion, formats, context budget and failures.
- `tests/memory/test_knowledge_postgres.py` (8 tests, marker `db`): real migration, fresh-store read, ACL revocation, cross-tenant canary, section scope, versions, approval and purge.
- `tests/voice/test_voice_app_settings.py`: tool/instruction/health wiring and settings redaction.
- `tests/tools/test_catalog.py`: the catalogue and the source `ToolDef` agree.
