# Local Development (Apple Silicon, M4 Pro / 24 GB)

**Honest status:** the repository has no code, no `pyproject.toml`, no tests. Steps marked *(planned)* do not work yet. Facts about this machine on 2026-10-19: system `python3` is **3.9.6** (too old for planning purposes: use 3.11+, exact version to be pinned when `pyproject.toml` exists), Homebrew and `ffmpeg` present, `uv` not installed.

## Stage A – tooling (planned)
```bash
brew install python@3.12 uv      # then verify: python3.12 --version
```
Phase 1 install: Python 3.13 is already present via Homebrew on this machine; `python3.13 -m venv .venv && .venv/bin/pip install pytest`; `pyproject.toml` requires >=3.11.

## Stage B – Gemini key (planned)
Create a key in Google AI Studio; put it in a git-ignored `.env` (copy from `.env.example`). Never paste it in chat, logs, or code. Set `TARA_LLM_MODEL` after checking the models page.

## Stage C – models (do NOT run until Phase 2 starts)
Why each: STT (Whisper family), VAD (Silero ~2 MB, S11), TTS (Kokoro 82M, needs `espeak-ng` via `brew install espeak-ng`, S8). Exact download commands and sizes are recorded when each integration is built; disk sizes are currently **unknown**. Verify: tiny script transcribing a known WAV / synthesizing a sentence. Benchmark: record wall time, peak RSS (`/usr/bin/time -l`), and real-time factor; write results to `docs/research/BENCHMARKS.md` (does not exist yet). Disable/replace: change `TARA_STT_BACKEND` etc. If too slow: smaller Whisper checkpoint, faster-whisper CPU int8, or cloud STT.

## Stage D – live audio (planned)
Grant microphone permission to the terminal app; list devices; record 5 s; transcribe; synthesize and play back.

## Stage E – meeting adapter (planned)
Virtual audio device (e.g. BlackHole) + Chrome automation for the PoC adapter; unverified. See [research/GOOGLE_MEET_OPTIONS.md](research/GOOGLE_MEET_OPTIONS.md).

## Test tiers (planned) `pytest -m offline` · `-m local_model` · `-m live_api`.
Common errors to watch: Kokoro needs `espeak-ng`; MPS ops fall back without `PYTORCH_ENABLE_MPS_FALLBACK=1`; pyannote weights require accepting terms and a Hugging Face token (S12).
