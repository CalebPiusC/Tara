# ADR-0010: Local embeddings by default behind an Embedder interface
**Status:** Proposed
**Decision:** default `bge-small-en-v1.5` (384 dims, MIT, 512 tokens); Gemini embeddings selectable per tenant. Model id and dim stored per chunk. **Reasons:** document text stays local; no per-call cost. **Risks:** English-only model; quality on our corpus unknown; 512-token chunk ceiling. **Validation:** A/B on the eval set; measure latency on M4 Pro. **Evidence:** model cards (R3, R4).
