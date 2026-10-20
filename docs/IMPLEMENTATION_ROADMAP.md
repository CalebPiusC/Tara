# Implementation Roadmap

Dependency order: P0 → P1 → P2 → (P3, P4 can overlap after P2) → P5 → P6 → P7 → P8. P4 is the schedule risk.

| Phase | Objective | Depends | Deliverables | Acceptance | Exit |
|---|---|---|---|---|---|
| 0 Verification | Docs + Meet path decision | — | this docs set, Meet feasibility spike notes | Spike: can a browser participant hear and speak 5 min? (yes/no recorded) | Path chosen or blocker declared |
| 1 Context & state | Deterministic core | 0 | `MeetingConfig` validator, state machine, participation gate, event contracts, `LocalMeetingAdapter`, fakes | Transition/decision/dedupe/staleness tests green offline | All simulation scenarios that need no models pass |
| 2 Voice pipeline | Local loop | 1 | VAD, STT, Gemini provider, Kokoro, cancellation, latency harness | Measured latency table; barge-in stops audio (measured); cancel tests | Baselines recorded; model setup documented from real runs |
| 3 Speaker intelligence | Attribution | 2 | hints fusion, optional diarizer, mapping versions | False-attribution rate measured on recorded sets; candidate-leak tests | Threshold calibrated from data |
| 4 Meeting integration | Real two-way media | 0,2 | chosen adapter, reconnect, participant info | Real meeting: hear, speak, interrupt, takeover, reconnect | Verified in live call, not mocks |
| 5 Interview engine | Interviews | 1,3 | plan, follow-ups, evidence store, reports | Single and group scenarios; evidence integrity tests | Human-reviewable reports |
| 6 General meetings | Address/proactive/observe | 1,3,4 | decisions/actions summaries | False activation / missed-address measured | Targets set from baselines |
| 7 Pitch & grounding | Knowledge | 1 | KnowledgeProvider, grounding validator | Unsupported-claim tests | No ungrounded claims in sims |
| 8 Avatar | Optional | 2,4 | provider eval, `AvatarProvider` impl prototype | Sync drift measured | Feasibility verdict |

Risks per phase: P0/P4 platform access & ToS; P2 latency/memory; P3 overlap & short utterances; P5 bias/consent; P8 hardware. Recommended first implementation task: **Phase 0 Meet spike plus Phase 1 state machine in parallel** (spike is time-boxed and decides P4).

**Progress 2026-10-19:** Phase 1 partially done: `config.py` validator, `runtime.py` state machine (epochs, takeover, dedupe, approval), `participation.py` gate, 53 passing offline tests. Not yet: NL→config extraction, event bus, `LocalMeetingAdapter`, simulation harness, persistence. Phase 0 spike outline: [research/PHASE0_MEET_SPIKE.md](research/PHASE0_MEET_SPIKE.md) (not run).

**Progress update:** engine code now exists for Phases 1 (core), most of 2's software shape (audio segmenter, turn estimator, provider adapters), 3's fusion logic, 5's interview engine and 7's grounding checks, all verified only against fakes (158 offline tests after hardening). Phase 2 acceptance (measured latency, real models) and Phase 4 (real meeting) are untouched. Do not read the existence of code as phase completion.

## Memory and RAG track (parallel to the phases above; Designed)
Details: [MEMORY_AND_RAG](MEMORY_AND_RAG.md). Do not start before Phase 2 audio latency is measured, since retrieval time shares the response budget.
| Stage | Objective | Deliverables | Exit |
|---|---|---|---|
| R1 Foundation | Postgres+pgvector on the dev Mac; minimal `knowledge_sources`/`document_versions`/`document_chunks`; basic ingest (Markdown first) and FTS retrieval with scope filter; eval corpus and FTS baseline | migrations, `KnowledgeStore` Postgres impl behind the Protocol, eval harness | baseline metrics recorded with command and date; canary tests pass |
| R2 Conversation memory | working memory, rolling summary, `memory_records`, candidate decisions/actions, same-meeting recall | consolidator, promotion rules | scripted-meeting tests incl. unconfirmed-as-confirmed = 0 |
| R3 Context assembly | token budgets, dedupe, trust-labelled prompt, fallbacks | `ContextAssembler` replacing the fixed prompt in `conversation.py` | budget and fallback tests; token/latency metrics logged |
| R4 Security and reliability | RLS decision, update/delete propagation, audit log, outage handling | deletion and version-swap tests | unauthorized retrieval rate zero across the suite |
| R5 Advanced retrieval | vector leg + fusion tuning if not done, reranker only if eval shows a ranking problem, alternative embedders | measured comparisons | decisions recorded as ADRs |
Note: vector leg is introduced after the FTS baseline so each addition has measured value.

**Progress 2026-10-20:** unified runtime foundation implemented against fakes (see [RUNTIME_FOUNDATION](RUNTIME_FOUNDATION.md)); 219 offline tests. Phase 2 (real models, measurements) deliberately not started; the owner will say when.

## Revised plan after the Gemini Live decision (2026-10-20)
The original Phase 2 (download and benchmark Whisper, Qwen, Kokoro) is **cancelled**. Replacement order:
| Step | Objective | Exit |
|---|---|---|
| L0 (done, offline) | Live adapter, playback cancellation, gate, spoken controls, tools, notes, orchestration (time/location/weather/pages/research), reconnect, memory interfaces, metrics | 428 offline tests; see [GEMINI_LIVE](GEMINI_LIVE.md), [MEETING_CONTROLS](MEETING_CONTROLS.md) and [ORCHESTRATION](ORCHESTRATION.md) |
| L1 (smoke done 2026-10-20) | Real-service smoke test with the owner's key: connect, audio reply, sample rate | One text turn: 1771.9 ms connect, 1039.4 ms to first audio, 24 kHz, turn complete; full live behaviour suite not run |
| L2 (adapter built, not yet run on hardware) | Local audio-device adapter (mic in, speakers out, headphones first) | `AudioDeviceAdapter` and `python -m tara.voice.demo` exist and are fake-backend tested; spoken conversation, real interruption and reconnect still need a real-device run; then tune `hold_ms`, silence duration, barge-in |
| L3 | Google Meet transport spike (browser participant or vendor bot) | two-way audio proven in a real meeting, not mocks |
| L4 | Speaker attribution on top of per-participant streams, interview and pitch flows, memory/RAG (R1-R5 track) | per earlier phase criteria |
**L1 progress 2026-10-20:** one real text-turn smoke passed (numbers above); the full live behaviour suite is still not run.
**L2 progress 2026-10-21:** the local microphone/speaker adapter and demo CLI are implemented and covered by offline fake-backend tests; no real microphone/speaker conversation has been run yet.
Local models return only if a concrete need (offline, cost, privacy) is measured.
