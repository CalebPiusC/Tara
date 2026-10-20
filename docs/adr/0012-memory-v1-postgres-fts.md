# ADR-0012: Memory v1 uses PostgreSQL full-text search, not pgvector or Redis

**Status:** Accepted (2026-10-20)
**Context:** Tara needs durable, cross-session memory for explicit requests such as "remember this product description" and "what did I tell you about Testflow?". The repository already had memory interfaces, an in-memory meeting-note store and a designed PostgreSQL/pgvector model, but no database, migration or persistence. The live voice path must stay responsive and must not gain a database call per audio chunk or per turn.

**Options considered:**
1. PostgreSQL + full-text search first, with tenant/user filtering in SQL.
2. PostgreSQL + pgvector and local embeddings immediately (ADR-0009/0010).
3. Redis as the primary session/memory store.
4. SQLite or JSON files as the durable store.

**Decision:** Use PostgreSQL as the durable store and PostgreSQL full-text search (`tsvector`, `websearch_to_tsquery`, `ts_rank_cd`) plus an `ILIKE` fallback for short names. Do not add pgvector, an embedding model, Redis or a vector database in v1. Keep the `MemoryStore` protocol so a vector leg can be added behind it. Use a dedicated `memory` optional dependency (`psycopg[binary,pool]`), with the schema in `src/tara/memory/migrations/001_memory.sql`.

**Reasons:**
- Explicit memories, product names and short facts are well served by exact and full-text matching; vectors are not required to meet the first acceptance scenarios.
- One service (PostgreSQL) already exists locally and provides transactions, indexes and durable commits. Adding Redis would create a second source of truth and a cache-invalidation problem before there is evidence of need.
- The live path can query once at session start and once per explicit recall tool call. No audio-path query is introduced.
- pgvector is still a reasonable later step, but the cost and quality of embeddings are unmeasured. ADR-0009 remains the design for that step.

**Consequences:**
- Paraphrase-heavy recall may miss; the `ILIKE` fallback only covers short literal names. Measure before adding embeddings.
- No semantic deduplication; deduplication is exact-normalized text only.
- No document chunk ingestion or hybrid retrieval yet.
- The schema has no ACL tables or RLS; v1 relies on application SQL filtering by `tenant_id` and `user_id`, and the app builds the scope. This is weaker than the target design and must not be described as multi-tenant hardened.
- No Redis cache; an in-process cache is unnecessary for the current measured use and would add invalidation risk.

**Validation:** `tests/test_memory_postgres.py` runs against a real PostgreSQL database in a throwaway schema and verifies commit, fresh-store recall, tenant/user isolation with canaries, correction provenance, expiry, concurrent writes and failure honesty. The offline suite verifies the service, gate, tools and rendering. No real Gemini tool-calling test has run yet.

**Revisit when:** a hand-labelled eval shows full-text recall is insufficient; measured recall latency or database load justifies a cache; or a second application process needs shared session state (Redis candidate). At that point add a measured comparison, not a speculative service.
