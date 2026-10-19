# Changelog

Read this first if you are joining mid-stream. It says **where the project started, what changed, and what is real**. Newest entries on top. Format loosely follows Keep a Changelog. Dates are 2026 and local to the maintainer.

**Status vocabulary** (same as the docs): *Implemented* = code exists and tests ran. *Designed* = written down, no code. *Proposed* = recommendation, not agreed. *Unverified* = depends on an external fact or model nobody has run.

## How to pick up from here
1. Read [initial.md](initial.md) (product source of truth), then [docs/README.md](docs/README.md).
2. Run `python3.13 -m venv .venv && .venv/bin/pip install pytest && .venv/bin/python -m pytest -q`. Expect all green (count is in the latest entry below).
3. Open questions: [docs/research/OPEN_QUESTIONS.md](docs/research/OPEN_QUESTIONS.md). Roadmap: [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md).
4. Rules for contributors and agents: [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md).

---

## [Unreleased]

### 2026-10-19 (day 1, late): memory and RAG design
- **Designed:** memory and RAG: [MEMORY_AND_RAG](docs/MEMORY_AND_RAG.md), [CONTEXT_ASSEMBLY](docs/CONTEXT_ASSEMBLY.md), [MEMORY_DATA_MODEL](docs/MEMORY_DATA_MODEL.md), [MEMORY_AND_RETRIEVAL_SECURITY](docs/MEMORY_AND_RETRIEVAL_SECURITY.md), [RAG_AND_MEMORY_EVALUATION](docs/RAG_AND_MEMORY_EVALUATION.md), [research](docs/research/RAG_TECHNOLOGY_EVALUATION.md), ADRs 0009-0011, roadmap track R1-R5. Paths follow the existing flat `docs/` convention instead of new subfolders. No retrieval, database or embedding code written; no SQL was run; no model downloaded.
- **Recommendation:** Postgres + pgvector (exact search first), hybrid FTS + vector with rank fusion, local bge-small embeddings by default, no reranker in v1, memory records start as candidates.
- **Unverified:** every performance and quality claim; pgvector license text; Postgres RLS behavior; Gemini embedding data retention.
- **Added:** this changelog.

### 2026-10-19 (day 1): engine code against fakes
**Implemented** (offline, 144 tests passing at the time of writing; nothing here touched a real model, API key or meeting):
- `src/tara/config.py`: `MeetingConfig` schema `tara.meeting_config/v0` and validator (app policy intersects requested tools; observe mode forces silence; group interviews need an attribution threshold).
- `src/tara/runtime.py`: 12-state machine, epochs, takeover, bounded event dedupe, proposal approval (stale epoch/age, duplicate trigger, floor busy).
- `src/tara/participation.py`: deterministic gate over a semantic intent.
- `src/tara/classify.py`: rule and LLM intent classifiers, fuzzy name hint.
- `src/tara/audio.py`, `turn.py`, `events.py`: ring buffer, resampler, VAD segmenter, end-of-turn estimate, in-process event bus.
- `src/tara/identity.py`, `evidence.py`: versioned label maps, evidence fusion with `unknown`, candidate evidence store that cannot cross candidates.
- `src/tara/context.py`, `conversation.py`, `interview.py`, `knowledge.py`, `tools.py`, `reports.py`: context engine, prompt and response validation, interview engine, keyword knowledge, permission-gated tools, reports.
- `src/tara/engine.py`: async pipeline with cancellation, barge-in, takeover, reconnect, echo guard.
- `src/tara/providers/gemini.py`: REST client, tested with a fake transport only. `providers/local_models.py`: mlx-whisper, Kokoro, Silero wrappers, **untested, nothing downloaded**.
- `src/tara/sim.py`, `fakes.py`, `adapters/local.py`: simulation harness, fakes, local scripted meeting adapter.
- Two real runtime bugs were found by the simulation scenarios and fixed (takeover while idle or generating).

**Not done / Unverified:** real STT/TTS/VAD runs, live Gemini call (request shape and auth header unconfirmed), any Google Meet connection, any latency measurement, avatar.

### 2026-10-19 (day 1): research and documentation foundation
- **Added:** about 45 documents in `docs/` (product, architecture, context model, runtime, identity, audio pipeline, contracts, security, failure modes, testing, roadmap), 8 ADRs (all *Proposed*), research notes with sources and access dates, `AGENTS.md`, `CLAUDE.md`, `.gitignore`, `.env.example`.
- **Key finding:** the official Meet Media API is Developer Preview, closed to new signups, and documents receiving media only. Realistic proof of concept is an unofficial browser participant or a third-party bot API. Spike plan written, **not run**: [docs/research/PHASE0_MEET_SPIKE.md](docs/research/PHASE0_MEET_SPIKE.md).
- **Environment facts:** Apple M4 Pro, 24 GB. System `python3` is 3.9.6 (too old); Python 3.13 is installed via Homebrew and used for `.venv`.

### 2026-10-19 (day 1): starting point
- Repository contained only `initial.md` (identical copy `INITIAL.md`), an empty `README.md` and an empty `.gitignore`. No git repository, no code.
