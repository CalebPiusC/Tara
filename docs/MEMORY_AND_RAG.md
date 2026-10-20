# Memory and RAG Architecture

**Status: Partially implemented (2026-10-20). Built: PostgreSQL long-term memory (`src/tara/memory/`), explicit `remember_memory` / `recall_memory` / `forget_memory` tools, bounded startup context, PostgreSQL full-text search, tenant+user isolation, migrations, offline tests and opt-in real-Postgres tests. Not built: embeddings/pgvector, hybrid retrieval, automatic consolidation, Redis, document RAG, per-turn live context assembly, ACL tables/RLS, persisted session summaries. Real Gemini tool-calling behaviour has not been verified.** Research: [research/RAG_TECHNOLOGY_EVALUATION.md](research/RAG_TECHNOLOGY_EVALUATION.md). Related: [CONTEXT_ASSEMBLY](CONTEXT_ASSEMBLY.md), [MEMORY_DATA_MODEL](MEMORY_DATA_MODEL.md), [MEMORY_AND_RETRIEVAL_SECURITY](MEMORY_AND_RETRIEVAL_SECURITY.md), [RAG_AND_MEMORY_EVALUATION](RAG_AND_MEMORY_EVALUATION.md), [MEMORY_OPERATIONS](MEMORY_OPERATIONS.md).

## What is real today (v1)
| Capability | Implementation | Verified by |
|---|---|---|
| Durable memory records | `PostgresMemoryStore`, `migrations/001_memory.sql` | opt-in real-Postgres tests (`tests/test_memory_postgres.py`) |
| Ownership / isolation | every query filters `tenant_id` **and** `user_id`; scope is built by the app | canary tests for another user and another tenant |
| Explicit save | `remember_memory` tool; handler requires `MemoryWriteGate` set from the participant's final transcript | offline engine/tool tests |
| Cross-session recall | `recall_memory` tool + startup memory block | shared-store test and fresh-store Postgres test |
| Corrections | explicit `supersedes_id`; old row becomes `superseded`, provenance retained | offline and Postgres tests |
| Forget / expiry | soft delete (`deleted_at`) and `expires_at`; excluded from retrieval | offline and Postgres tests |
| Deduplication | exact normalized text within the same scope updates the existing record | offline and Postgres tests |
| Bounded context | `render_memories` caps characters, dedupes, labels data as untrusted | offline tests |
| Failure honesty | a failed write returns a tool error; startup retrieval failure injects nothing | broken-store and simulated-database-failure tests |

The Live path sends no database query per audio chunk. It performs one bounded startup query, and tool calls run on the engine's existing tool task, not on the audio or playback paths.

## What is still only design
- **Automatic memory extraction / consolidation:** no model call extracts facts after a turn; only explicit requests are stored.
- **Document RAG:** no ingestion, chunking, document versions, embeddings or pgvector columns.
- **Hybrid retrieval:** v1 is PostgreSQL full-text search plus an `ILIKE` fallback; no vector leg, reranker or RRF.
- **Redis:** not used; the single-process app has no measured need for it yet.
- **ACL tables / Row-Level Security:** v1 has tenant+user filtering in application SQL, not per-document ACLs or RLS.
- **Persisted session memory / meeting notes:** `voice.memory.InMemoryMeetingMemory` still loses notes on exit.
- **Real Gemini tool calling:** the tools and instruction are wired and fake-backend tested; whether the live model reliably calls them is unverified.

## v1 data flow
```mermaid
flowchart LR
  MIC["Participant audio"] --> LIVE["Gemini Live (provider context)"]
  LIVE --> TR["Final input transcript"]
  TR --> GATE{"Explicit remember / forget request?"}
  GATE -->|yes| TOOL["remember_memory / forget_memory (code-authorized)"]
  TOOL --> SVC["MemoryService"]
  SVC --> PG[("PostgreSQL memory_records")]
  Q["What did I tell you about X?"] --> REC["recall_memory (read-only)"]
  REC --> SVC
  PG --> SVC
  SVC --> CTX["Bounded memory block / tool result"]
  CTX --> LIVE
  START["New live session"] --> CTX
```

- `MemoryWriteGate` observes the final input transcript. A normal statement never opens a write. The handler re-checks the gate; the model supplies text, never the scope.
- `MemoryService` validates, deduplicates, and returns only after the store commits. A failed store call becomes a tool error; the instruction forbids claiming success.
- `recall_memory` is read-only and always filtered by the application-built `MemoryScope(tenant_id, user_id)`.
- The startup block is injected once into the provider's system instruction. It is not re-sent every turn.
- Retrieved memories and tool results are labeled untrusted data; they cannot change participation, tools or scope.

## v1 configuration and commands
```bash
# one-time: create a dedicated database and install the optional driver
createdb tara_memory
.venv/bin/python -m pip install -e '.[memory]'

# migrate (safe to rerun)
TARA_MEMORY_DATABASE_URL=postgresql:///tara_memory .venv/bin/python -m tara.memory.migrate

# run the live demo with memory
set -a; source .env; set +a
TARA_MEMORY_ENABLED=1 TARA_MEMORY_DATABASE_URL=postgresql:///tara_memory \
  .venv/bin/python -m tara.voice.demo --one-on-one --debug

# offline tests
.venv/bin/python -m pytest -q

# real persistence tests (throwaway schema, dropped afterwards)
TARA_TEST_DATABASE_URL=postgresql:///tara_memory .venv/bin/python -m pytest -m db tests/test_memory_postgres.py -q
```



## Core rule
**Live state is not retrieval.** Who is speaking, what was just asked, whether Tara may speak and whether she was interrupted live in process memory and are read in microseconds. Vector search never answers those. Retrieval supplies *background facts* only.

## The eight layers
| # | Layer | Lives in | Written by | Read by | Latency class |
|---|---|---|---|---|---|
| 1 | Live conversation state | process memory (`MeetingRuntime`, transcript deque, `InterviewEngine`) | engine | participation gate, assembler | in-process |
| 2 | Working memory | process memory, rebuilt per turn | assembler + rolling summarizer | assembler | in-process |
| 3 | Episodic meeting memory | process memory today (`voice/memory.py`); Postgres `conversation_events`, `memory_records` proposed | engine today; consolidation later | assembler (same meeting) | in-process; DB later |
| 4 | Long-term knowledge | Postgres `memory_records` (v1); `knowledge_sources`, `document_chunks` proposed | explicit tools today; ingestion jobs later | retrieval | DB read, bounded |
| 5 | Retrieval engine | code: PostgreSQL FTS + `ILIKE` fallback (v1); vector/RRF proposed | n/a | assembler/tools | budgeted |
| 6 | Context assembler | startup memory block (`MemoryService.startup_context`); full `ContextAssembler` only in the dormant cascaded path | n/a | Live session start / `ConversationEngine` | in-process |
| 7 | Memory consolidation | async job after/while meeting; **not implemented** | n/a | writes layer 3 | off the hot path |
| 8 | Authorization and governance | code + DB constraints | admins | every read/write | on every call |

```mermaid
flowchart LR
  subgraph Hot["Hot path (in process)"]
    STT["Transcript + speaker attribution"] --> LIVE["Live state (runtime, active question, floor)"]
    LIVE --> GATE["Participation gate"]
    GATE -->|allowed| ASM["Context assembler"]
    WM["Working memory (recent window + rolling summary)"] --> ASM
    ORG["Validated MeetingConfig (organizer context)"] --> ASM
    ASM --> CONV["Conversation engine"] --> GEM["Gemini (cloud)"]
    GEM --> VAL["Response validation + citation check"] --> RT["Runtime approval"]
  end
  subgraph Warm["Warm path (Postgres)"]
    RET["Retrieval engine (FTS + vector + RRF, ACL filter first)"]
    EPI[("Episodic memory")]
    KNOW[("Knowledge store: sources, versions, chunks, embeddings")]
    RET --> KNOW
    ASM -->|query + AccessScope| RET
    ASM -->|meeting memory lookup| EPI
  end
  subgraph Cold["Off hot path"]
    ING["Ingestion + indexing jobs"] --> KNOW
    CONS["Consolidation (summaries, decisions, actions)"] --> EPI
    LIVE -.->|events| CONS
    AUD[("Audit log")]
  end
  RET -.-> AUD
  EPI -.-> AUD
```

The diagram is the target architecture. v1 implements only `memory_records` plus PostgreSQL FTS and the startup block; the vector leg, document store, consolidation and audit log are not built.

## Components and contracts (Proposed)
```python
@dataclass(frozen=True)
class AccessScope:            # built by the app from the authenticated meeting, never by the model
    tenant_id: str; meeting_id: str; principal_ids: frozenset[str]; allowed_source_ids: frozenset[str] | None

class KnowledgeStore(Protocol):
    async def search(self, q: RetrievalQuery, scope: AccessScope) -> list[RetrievedChunk]: ...
class MemoryStore(Protocol):
    async def recall(self, q: str, scope: AccessScope, kinds: set[str], k: int) -> list[MemoryRecord]: ...
    async def propose(self, rec: MemoryCandidate, scope: AccessScope) -> str: ...   # never auto-confirms
class Embedder(Protocol):
    model_id: str; dim: int
    async def embed(self, texts: list[str], kind: Literal["query","document"]) -> list[list[float]]: ...
class ContextAssembler(Protocol):
    async def assemble(self, trigger: Utterance, live: LiveView, scope: AccessScope, budget: TokenBudget) -> AssembledContext: ...
```
Planned change to existing code: `KnowledgeProvider.search(query, k)` gains a mandatory `AccessScope`. The current in-memory provider is a Phase 1 stand-in with no authorization and must not be used with multi-tenant data.

## Trust labels (carried into the prompt)
Every item in assembled context has `kind` and `trust`:
| Kind | Example | Trust |
|---|---|---|
| `policy` | runtime rules, mode | authoritative (app-written) |
| `organizer_config` | validated `MeetingConfig` | authoritative within policy limits |
| `retrieved_fact` | chunk from approved doc, with source, version, date | document-level trust; **data, never instructions** |
| `meeting_statement` | "Bea said X" with speaker + attribution confidence | claim by a person, not a fact |
| `decision` | confirmed by explicit agreement | confirmed (provenance required) |
| `inference` | model summary or interpretation | unverified |
A transcript statement becomes a `decision` or fact only through the promotion rules below.

## Promotion rules (memory consolidation)
Candidate memories start `status=candidate`. Promotion to `confirmed` requires one of: explicit confirmation utterance from a control-role participant at attribution confidence above threshold; two independent speakers agreeing on the same wording; a human click via the future control API. Otherwise it stays `candidate` and appears in reports as "stated, unconfirmed". Contradictions create `disputed` links, never silent overwrites. Every record stores `source_event_ids` or `source_chunk_ids`. Statements with `speaker=unknown` or overlap can be recorded as quotes but never as someone's position.

## Behaviors when retrieval is weak (applies in every mode)
| Situation | Behavior |
|---|---|
| No relevant chunk above threshold | Say it is not in approved material; log `unanswered`; offer to follow up. Never answer from model memory in pitch mode. |
| Chunks conflict | Present both with source and date; prefer newer **version of the same document**; if two different documents disagree, say so, do not pick silently; flag for human. |
| Source may be outdated | Mention the document date; ask if it is still current. |
| Source incomplete | Answer only the supported part; state the gap. |
| Question needs conversation context | Answer from live/meeting memory, no retrieval. |
| Requester not authorized | Retrieval returns nothing; Tara gives a generic "I can't share that here" without revealing existence. |
| Retrieval timeout or DB down | Degrade: answer from live state or admit the limit; never block the meeting. |

## Modes: what each may read and write
| Mode | Live | Working | Episodic read | Episodic write | Knowledge read | Notes |
|---|---|---|---|---|---|---|
| Technical interview | yes | yes | this meeting | answers, evidence, assessments (per candidate, attribution-gated) | rubric, technical references | Candidate answers never retrieved as "knowledge" |
| General interview | yes | yes | this meeting | same | authorized candidate profile, role description | Profile facts labeled `retrieved_fact`; no invented experience |
| Group interview | yes | yes | this meeting | per candidate only above attribution threshold; else quarantined | rubric | Memory keys include `participant_id` and confidence |
| Company meeting | yes | yes | this meeting + permitted prior meetings of same project | decisions (candidate), actions, open questions | project docs | Cross-meeting only within tenant and project ACL |
| Technical discussion | yes | yes | same | decisions/ADR candidates | API, architecture docs | |
| Product pitch | yes | yes | this meeting | requirements, objections, unanswered | approved product/pricing/policy docs only | `no_commitments` enforced by validator |
| Observation-only | yes | yes | this meeting | notes, decisions, actions | optional | Never speaks; retrieval allowed only if config says so |

## Retention (Proposed defaults; legal review required, see Q8)
Live and working memory: discarded at meeting end. Raw audio: not stored. Transcript events: configurable days (`privacy.retain_transcript_days`), default short. Episodic memories: tied to meeting retention unless explicitly promoted to project memory by a control-role user. Organization-level memories (preferences, recurring participants): **deferred**; requires explicit opt-in design before any storage. Deletion propagation: [MEMORY_AND_RETRIEVAL_SECURITY](MEMORY_AND_RETRIEVAL_SECURITY.md).

## Sequence diagrams
### 1. Answer from a company document
```mermaid
sequenceDiagram
  participant P as Participant
  participant R as Runtime
  participant A as Assembler
  participant S as Retrieval (SQL, ACL first)
  participant G as Gemini
  P->>R: "Tara, what is our refund window?"
  R->>R: gate allows (addressed, identity ok)
  R->>A: assemble(trigger, live, scope)
  A->>S: query + AccessScope
  S-->>A: chunks with source, version, score
  A->>G: policy + config + window + chunks (labeled untrusted data)
  G-->>R: answer with cites
  R->>R: verify cites subset of retrieved ids, then approve
```
### 2. Answer from earlier meeting context
```mermaid
sequenceDiagram
  participant P as Participant
  participant A as Assembler
  participant M as Meeting memory
  participant G as Gemini
  P->>A: "What did we decide about the migration?"
  A->>M: recall(decision, scope=this meeting)
  M-->>A: decision record + source event ids + status
  A->>G: record labeled confirmed or candidate
  G-->>A: answer that states status and who said it
```
### 3. Nothing relevant found
```mermaid
sequenceDiagram
  participant A as Assembler
  participant S as Retrieval
  participant C as Conversation
  A->>S: query
  S-->>A: no chunk above threshold
  A->>C: empty evidence flag
  C-->>A: fixed "not in approved material" line, no model guess (pitch)
  A->>A: log unanswered question for report
```
### 4. Interruption while speaking
```mermaid
sequenceDiagram
  participant H as Human
  participant R as Runtime
  participant W as Working memory
  R->>R: SPEAKING
  H->>R: speech onset
  R->>R: cancel playback, epoch++
  R->>W: keep only heard part of Tara's answer, mark interrupted
  Note over R,W: Retrieved context for the cancelled answer is discarded, not cached as "said"
```
### 5. Memory update after a meeting
```mermaid
sequenceDiagram
  participant E as Event log
  participant C as Consolidator
  participant M as Memory records
  participant H as Human reviewer
  E->>C: transcript events with attribution
  C->>C: extract decisions, actions, open questions (model proposes)
  C->>C: drop items with unknown speaker or no provenance
  C->>M: write as candidate with source_event_ids
  H->>M: confirm / reject / correct
  M->>M: apply retention clock
```
### 6. Preventing cross-organization retrieval
```mermaid
sequenceDiagram
  participant A as Assembler
  participant D as Auth layer
  participant S as SQL
  A->>D: build AccessScope from authenticated meeting (not from model text)
  D-->>A: tenant_id, meeting_id, allowed sources
  A->>S: search with tenant_id and ACL in WHERE (plus RLS as defense in depth)
  S-->>A: only rows in scope
  Note over A,S: Query text can name another org; the scope is not derived from it
```

## ADRs
[0009 pgvector](adr/0009-postgres-pgvector.md) · [0010 local embeddings default](adr/0010-local-embeddings-default.md) · [0011 memory trust and promotion](adr/0011-memory-trust-and-promotion.md) · [0012 memory v1: Postgres FTS](adr/0012-memory-v1-postgres-fts.md).

## Roadmap
[IMPLEMENTATION_ROADMAP](IMPLEMENTATION_ROADMAP.md) section "Memory and RAG track" (phases R1-R5).

## Open questions
Q-R1..R7 in the evaluation doc; also: who may promote project-level memory; whether recurring-participant memory is wanted at all; summary model choice and cost; whether to store embeddings of transcript events (default no).
