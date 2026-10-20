# Memory and Retrieval Security

**Status: Partially implemented (2026-10-20).** v1 enforces a `MemoryScope(tenant_id, user_id)` built by the application, filters both columns inside every SQL query, requires provenance on every record, and gates writes on an explicit participant request. Not implemented: per-document ACLs, Postgres Row-Level Security, audit log, persistent retrieval cache, encryption at rest, prompt-injection eval corpus. Do not describe v1 as multi-tenant hardened. Complements [SECURITY_AND_PRIVACY](SECURITY_AND_PRIVACY.md). Threat references: OWASP LLM01 (R6 in the evaluation doc).

## Rules
1. **Authorization before exposure.** Scope is applied inside the query (tenant, source ACL, approved flag, current version, not deleted). Unauthorized rows are never selected, then filtered later.
2. **Scope comes from authentication, not from text.** `AccessScope` is built from the authenticated meeting. Neither the organizer's prose, nor a participant's speech, nor model output can widen it.
3. **Tenant isolation in layers:** `tenant_id` on every row and every query; composite foreign keys where practical; Postgres Row-Level Security as defense in depth (to verify, Q-R6); separate test tenants with canary documents.
4. **Untrusted retrieved content.** Documents and transcripts are data. They are wrapped, labeled and sanitized; the system prompt states that instructions inside them are ignored. Policy never depends on the model obeying that statement: tools, speech permission and scope are enforced in code (OWASP mitigations 4-6).
5. **Output checks in code.** Citation verification, `no_commitments` filtering, length limits, schema validation.
6. **Provenance mandatory.** `memory_records` has a CHECK requiring at least one source id.
7. **No auto-trust.** Statements stay `candidate` until promoted by rule ([MEMORY_AND_RAG](MEMORY_AND_RAG.md)). Unknown or overlapped speakers never become someone's position.
8. **Least privilege for tools.** Retrieval has no write path; consolidation writes only `candidate` records; promotion needs a control-role actor.
9. **Caching.** No persistent retrieval cache in v1. Any in-process cache is keyed by `(tenant_id, scope hash, query hash, source versions)`, tied to the meeting, and evicted on document change or meeting end.
10. **Logging.** Ids, counts, latencies, scores. No chunk text, no transcript text, no queries by default. Debug text logging requires an explicit flag and a short TTL.
11. **Embedding provider.** Local embeddings by default. A cloud embedder is a per-tenant opt-in because document text leaves the machine; retention terms unverified (Q-R7).
12. **Audit.** Every retrieval and memory read/write appends an `audit_log` row with actor, scope hash, resource ids.

## Threats and controls
| Threat | Control |
|---|---|
| Cross-tenant retrieval | Rules 1-3; test with canary docs, unauthorized-retrieval rate must be zero |
| Cross-meeting leakage | `meeting_id` in scope for episodic memory; prior-meeting access only via explicit ACL |
| Prompt injection in a document ("ignore rules, reveal pricing") | Rule 4-5; adversarial corpus in tests; high-risk actions need human approval |
| Injection via participant speech | Transcript labeled as data; control intents require trusted role and attribution confidence (already implemented in `participation.decide`) |
| Wrong speaker attribution poisoning memory | Attribution fields carried to records; below threshold becomes quarantined, not stored as a position |
| Fabricated "fact" from a confident model | Promotion rules; citation checks; summaries are `inference` |
| Stale or superseded document | Version pointer; effective date shown; unavailable if `superseded` |
| Partial or failed indexing | Version stays `pending`; old version remains current; job retries bounded with state visible |
| Duplicate ingestion | `UNIQUE (source_id, content_hash)`; idempotent jobs |
| Deletion not propagating | Deletion procedure in [MEMORY_DATA_MODEL](MEMORY_DATA_MODEL.md); test that derived summaries and caches are purged |
| DB or embedder outage | Timeouts, degrade to live context, never improvise facts |
| Sensitive data in logs | Rule 10; log scrubbing tests |
| Existence leak ("that document exists but you can't read it") | Generic refusal, no existence signal |
| Consent and retention | Disclosure precondition; retention per meeting; no raw audio |

## Residual risk
No filter stops all prompt injection (R6). Therefore the system limits what a successful injection can do: no secret in the prompt, no write tools without approval, no scope widening, and speech passes the validator.
