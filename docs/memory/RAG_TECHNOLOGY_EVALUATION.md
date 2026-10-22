# RAG Technology Evaluation

Researched **2026-10-19**. Status: Proposed. Nothing below was benchmarked by us. Rows say "Unknown; requires verification" where no fetched source established the fact.

## Sources (fetched this date; summaries by a fetch tool)
| ID | URL | Established |
|---|---|---|
| R1 | https://github.com/pgvector/pgvector | HNSW and IVFFlat; exact search without an index; operators `<->` L2, `<#>` neg. inner product, `<=>` cosine; index dimension limits: `vector` 2,000, `halfvec` 4,000; storage up to 16,000 dims; **approximate indexes filter after the index scan so filtered results can be sparse**; `hnsw.iterative_scan` mitigates; partial indexes and partitioning suggested; hybrid with Postgres full-text via Reciprocal Rank Fusion or a cross-encoder; Postgres 13+; `brew install pgvector` adds the extension to the `postgresql@17` and `@18` formulas. License not stated in the fetched text. |
| R2 | https://www.postgresql.org/docs/current/textsearch-controls.html | `websearch_to_tsquery` never raises syntax errors on user input; `ts_rank`/`ts_rank_cd`; GIN index on stored `tsvector`; **no global statistics (no IDF)**; ranks are relative to a query and not comparable across queries. |
| R3 | https://ai.google.dev/gemini-api/docs/embeddings | `gemini-embedding-2` (stable, multimodal, 8,192 input tokens, 128-3072 dims, recommended 768/1536/3072, auto-normalized when truncated, no `task_type`, task prefix in prompt) and `gemini-embedding-001` (text, 2,048 tokens, `task_type`, manual normalization). **Embedding spaces are incompatible: upgrading means re-embedding.** Page does not describe data retention. |
| R4 | https://huggingface.co/BAAI/bge-small-en-v1.5 | 33.4M params, 384 dims, 512-token max, MIT, MTEB retrieval average 51.68 (15 tasks) per model card; query instruction optional ("slight degradation" without). Disk size not stated (about 130 MB is our estimate from F32 weights, unverified). |
| R5 | https://huggingface.co/BAAI/bge-reranker-base | Cross-encoder about 0.3B params, MIT, English and Chinese, scores unbounded (compare relatively), example reranks top 100 to top 3, "more accurate but less efficient". |
| R6 | https://genai.owasp.org/llmrisk/llm01-prompt-injection/ | Indirect prompt injection via external content (documents) is a named risk; no foolproof prevention; mitigations: constrain behavior, validate outputs in code, filter, least privilege handled in code, human approval for high-risk actions, **segregate and label untrusted content**, adversarial testing. |
| R7 | https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/ | Names RAG metrics (context precision, context recall, faithfulness, response relevancy, noise sensitivity). Inputs and LLM-judge requirements **not established** by the fetched page. |

## 1. Is PostgreSQL + pgvector enough?
| Criterion | Postgres + pgvector | Dedicated vector DB (Qdrant, Weaviate, etc.) | Implication for Tara |
|---|---|---|---|
| Vector search | HNSW/IVFFlat or exact (R1) | Unknown; not researched | Corpus per organization is expected to be small (docs, not web-scale): exact search or HNSW is fine. Size to be measured. |
| Keyword search | Built in FTS with GIN (R2); no IDF | Varies; not researched | FTS is adequate as the lexical leg; weakness: term rarity is ignored, so fusion matters. |
| Metadata and ACL filtering | Plain SQL `WHERE`, joins, transactions | Unknown | **Authorization is the hardest requirement and SQL gives it transactionally, in the same query.** |
| Filtered ANN | Post-filter caveat (R1) | Unknown | Use exact search for small tenants, iterative scans or partial indexes later. Test with canary tenants. |
| Operations | One database for relational data, memory, vectors | Extra service | Matches ADR-0001 "no new infrastructure without need". |
| Local dev on Apple Silicon | `brew install pgvector` (R1), requires Postgres 17 or 18 formulas | Unknown | One brew install; exact commands verified in Phase R1, not now. |
**Decision (Proposed, confidence medium-high):** Postgres + pgvector. A dedicated vector DB needs evidence we do not have: measured recall or latency failure at realistic scale. Revisit trigger: P95 retrieval latency over budget or recall below target on the eval set after tuning.

## 2. Retrieval method
| Method | Pro | Con |
|---|---|---|
| Keyword (FTS) | Exact terms, product names, error codes, acronyms | No synonyms; no IDF (R2) |
| Vector | Paraphrase, spoken-style questions | Misses rare identifiers; model-specific |
| **Hybrid (both, fused)** | Covers both; spoken questions from ASR are noisy so both help | Two queries, fusion tuning |
**Fusion:** Reciprocal Rank Fusion is named by R1 as an option. It uses ranks only, which avoids mixing incomparable scores (R2: ts ranks are not comparable across queries; R5: reranker scores unbounded). The usual constant (k=60) is from memory of the RRF paper and is **unverified**; treat it as a tunable.
**Relevance threshold:** cannot be a fixed score from either leg. Use calibrated thresholds from the eval set, plus "agreement" signals (a chunk found by both legs). Open question Q-R3.

## 3. Embedding model
| Criterion | bge-small-en-v1.5 (local) | gemini-embedding-2 (cloud) | gemini-embedding-001 (cloud) |
|---|---|---|---|
| Runs locally | Yes (33.4M params) | No | No |
| Data leaves machine | No | Yes; retention terms not on fetched page | Yes |
| Dimensions | 384 | 128-3072 (use 768/1536 to stay under the 2,000 `vector` index limit) | same |
| Max input | 512 tokens | 8,192 tokens | 2,048 tokens |
| License | MIT | API terms | API terms |
| Languages | English model | Multilingual claimed by name only; unverified | same |
| Latency on M4 Pro | Unknown; requires verification | network-bound, unmeasured | same |
| Quality on our data | Unknown; requires evaluation | Unknown | Unknown |
**Recommendation (Proposed, confidence low-medium until the eval set exists):** default local `bge-small-en-v1.5` for privacy and zero API cost; keep an `Embedder` interface so Gemini embeddings can be A/B tested. **Store `embedding_model` and `dim` on every chunk** and never mix spaces in one search; changing model = re-index job (R3 states incompatibility). Download deferred to the retrieval phase.

## 4. Reranking
bge-reranker-base is a 0.3B cross-encoder (R5). Costs a model load and per-candidate inference during a live conversation. **Not in v1.** Add only if the eval shows correct chunks retrieved but ranked below the budget cutoff (Phase 5 gate).

## 5. Parsing and chunking
Not benchmarked. Starting point (Proposed): structure-aware chunks (split on headings, keep tables as units, keep code fences intact), target size well under the embedder limit (512 tokens for bge-small, so about 200-350 tokens with overlap), each chunk prefixed with its heading path. Parser libraries not yet evaluated (Q-R5).

## 6. Evaluation tooling
Ragas lists relevant metrics (R7) but whether it needs an LLM judge is not established here. First build a small hand-labelled set and compute precision@k, recall@k, MRR in plain Python ([RAG_AND_MEMORY_EVALUATION.md](RAG_AND_MEMORY_EVALUATION.md)). Adopt a framework only after that proves insufficient.

## Open questions
Q-R1 corpus size per tenant. Q-R2 exact vs HNSW cutover. Q-R3 relevance threshold method. Q-R4 chunk size on real docs. Q-R5 parser choice (PDF, DOCX, HTML, Markdown). Q-R6 RLS as defense in depth (Postgres RLS docs not fetched). Q-R7 Gemini embedding data-retention terms.
