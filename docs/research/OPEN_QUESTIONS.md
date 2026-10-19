# Open Technical Questions

| ID | Question | How to resolve | Blocks |
|---|---|---|---|
| Q1 | Which real Meet path works (browser participant vs vendor bot vs Media API)? | Spike D and E ([GOOGLE_MEET_OPTIONS.md](GOOGLE_MEET_OPTIONS.md)) | Phase 4 |
| Q2 | Can speaker identity be derived from Meet DOM speaking indicators with useful accuracy? | Measure on recorded test meeting with ground truth | Phase 3/5 |
| Q3 | Whisper runtime on M4 Pro: mlx-whisper vs whisper.cpp vs faster-whisper CPU int8; latency per VAD segment | Benchmark script ([LOCAL_DEVELOPMENT.md](../LOCAL_DEVELOPMENT.md)) | Phase 2 |
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
