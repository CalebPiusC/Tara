# ADR Index
| ADR | Title | Status |
|---|---|---|
| [0001](0001-modular-monolith.md) | Modular monolith | Proposed |
| [0002](0002-cascaded-vs-live-model.md) | Cascaded STT→LLM→TTS vs speech-to-speech | Superseded by 0013 |
| [0003](0003-meeting-integration-strategy.md) | Meeting integration strategy | Proposed |
| [0004](0004-event-transport.md) | In-process event bus; WebSocket only at the edge | Proposed |
| [0005](0005-speaker-attribution-strategy.md) | Speaker attribution as evidence with `unknown` | Proposed |
| [0006](0006-provider-abstraction.md) | Provider protocols and config-held model ids | Proposed |
| [0007](0007-runtime-owns-actions.md) | Models propose, runtime approves (epochs) | Proposed |
| [0008](0008-avatar-extension.md) | Avatar behind `AvatarProvider` | Proposed |
| [0009](0009-postgres-pgvector.md) | PostgreSQL with pgvector | Proposed |
| [0010](0010-local-embeddings-default.md) | Local embeddings by default | Proposed |
| [0011](0011-memory-trust-and-promotion.md) | Memory trust and promotion | Proposed |
| [0012](0012-unified-runtime-routing.md) | Unified runtime, rule-bounded routing | Proposed |
| [0013](0013-gemini-live-primary.md) | Gemini Live primary, local models dormant | Accepted (unverified live) |
| [0014](0014-orchestration-tools.md) | Orchestration as read-only tools in the Live path | Accepted (implemented offline; live search unverified) |
| [0015](0015-automatic-meeting-memory.md) | Automatic, meeting-scoped memory | Accepted (implemented offline and on real PostgreSQL; retrieval/consolidation extended by 0016) |
| [0016](0016-memory-retrieval-consolidation-recovery.md) | Retrieval, consolidation, corrections and restart recovery | Accepted (implemented offline and on real PostgreSQL; live behaviour unverified; document-RAG deferral superseded by 0017) |
| [0017](0017-document-rag.md) | Document RAG with approved documents, versions and read ACLs | Accepted (implemented offline and on real PostgreSQL; live Gemini tool calling unverified) |
