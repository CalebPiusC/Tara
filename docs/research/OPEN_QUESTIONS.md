# Open Technical Questions

| ID | Question | How to resolve | Blocks |
|---|---|---|---|
| Q1 | Which real Meet path works (browser participant vs vendor bot vs Media API)? | Spike D and E ([GOOGLE_MEET_OPTIONS.md](../google-meet/GOOGLE_MEET_OPTIONS.md)) | Phase 4 |
| Q2 | Can speaker identity be derived from Meet DOM speaking indicators with useful accuracy? | Measure on recorded test meeting with ground truth | Phase 3/5 |
| Q3 | Whisper runtime on M4 Pro: mlx-whisper vs whisper.cpp vs faster-whisper CPU int8; latency per VAD segment | Benchmark script ([LOCAL_DEVELOPMENT.md](../getting-started/LOCAL_DEVELOPMENT.md)) | Phase 2 |
| Q4 | Kokoro time-to-first-audio per sentence on MPS vs CPU | Benchmark | Phase 2 |
| Q5 | Gemini rate limits, latency distribution, data retention terms for the chosen model | Read pricing/terms pages; measure | Phase 2 |
| Q6 | Is pyannote usable at low latency on M4 Pro, or only offline relabeling? | Benchmark | Phase 3 |
| Q7 | Echo: does the meeting platform return Tara's own audio to her input? | Test in real meeting | Phase 4 |
| Q8 | Legal/consent requirements for AI in interviews per jurisdiction | Counsel review; out of engineering scope | Phase 5 |
| Q9 | Is the end-to-end budget (end of utterance → first audio) achievable with cascaded STT→LLM→TTS? | Measure; fallback evaluate Gemini Live API | Phase 2 |
| Q10 | Wake/addressing detection: LLM classifier vs rules+LLM; false-positive rate | Simulation corpus | Phase 6 |

## Memory and RAG
| ID | Question | Resolve by |
|---|---|---|
| Q-R1 | Realistic corpus size per tenant | ask product owners; sample real docs |
| Q-R2 | Exact vs HNSW cutover | benchmark on eval corpus |
| Q-R3 | Relevance threshold method across FTS and vector | calibrate on eval set |
| Q-R4 | Chunk size and overlap on real documents | chunking eval |
| Q-R5 | Parser choice (PDF, DOCX, HTML, Markdown) | spike |
| Q-R6 | Use Postgres Row-Level Security as defense in depth? | read official docs, prototype |
| Q-R7 | Gemini embedding data retention terms | read terms page |
| Q-C1 | Token counting method for the chosen Gemini model | verify docs |
| Q-C2 | Is query rewriting worth its latency? | measure |
| Q-C3 | Summarizer model and cost | measure |
| Q-C4 | Topic-change detection quality | scripted meetings |

## Unified runtime
| ID | Question | Resolve by |
|---|---|---|
| Q-U1 | Does the mlx tokenizer pass `enable_thinking`; does `make_sampler` exist with this signature? | load Qwen in Phase 2 |
| Q-U2 | Is Qwen3-0.6B good enough for intent, routing, and short answers? How calibrated is its confidence? | labelled routing/intent eval set |
| Q-U3 | Keep all three models loaded vs on-demand on 24 GB with real usage? | measure RSS and latency |
| Q-U4 | Which routing keywords misroute on real questions? | collect questions from simulations |
| Q-U5 | Gemini auth header and schema field casing | one live call with a key |
| Q-U6 | Android footprint of this stack | out of scope until the laptop path is measured |

## Gemini Live (2026-10-20)
| ID | Question | Resolve by |
|---|---|---|
| Q-L1 | Does the chosen model id connect on the Developer API with our key? | `python -m tara.voice.smoke` |
| Q-L2 | Order of input transcription vs first response audio; does transcription arrive early enough for the gate? | real session logs (event kinds only) |
| Q-L3 | Real connect, first-audio and interrupt-to-silence latency | metrics from a real session |
| Q-L4 | Is discarded (gated or interrupted) generation billed? Can it be cut early? | billing page, usage metadata |
| Q-L5 | Tool behavior per model (blocking, NON_BLOCKING, cancellation message) | opt-in tool test |
| Q-L6 | Does resumption actually preserve context across reconnect; GoAway lead time | long session test |
| Q-L7 | Does `hybrid_end_of_speech` improve latency? Best `silence_duration_ms`? | A/B with real audio |
| Q-L8 | Is local energy VAD good enough for barge-in, or do we need a better VAD? | noisy-room test (VAD choice is a code dependency, not a model download we plan) |
| Q-L9 | Can we gate earlier (manual activity signals) to avoid generating unaddressed replies? | prototype |

## Conversational behaviour (2026-10-21)
| ID | Question | Resolve by |
|---|---|---|
| Q-B1 | Does `gemini-3.8-live` produce audible laughter or other non-verbal sounds at all, and can instructions steer it? | listening test with a real key (not automatable) |
| Q-B2 | Does the persona instruction measurably reduce stock openers, lists and over-length replies? | `tests/live/test_behaviour_live.py` over many runs |
| Q-B3 | Does a mid-session style note change later replies, and does it ever trigger a spoken reply? | live note test plus A/B with guidance on and off |
| Q-B4 | Is the 2.5 s floor wait and 1.5 s hold acceptable in real meetings, or too slow or too eager? | real-meeting timing data |
| Q-B5 | How often do the English lexical signals misfire (urgency in casual speech, missed jokes)? | labelled transcript set; consider a cheap classifier |
| Q-B6 | Does proactive audio (always on) reduce responses to background noise enough to matter? | noisy-room session metrics |
| Q-B7 | Does the model honour "no human experience claims" and admit uncertainty reliably? | rubric flags over adversarial prompts |
