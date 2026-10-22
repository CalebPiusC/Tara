# Local Development (Apple Silicon, M4 Pro / 24 GB)

**Updated 2026-10-21: no local model is downloaded or needed.** Gemini Live is the voice engine ([GEMINI_LIVE](../engine/GEMINI_LIVE.md)); this machine only runs Python, the tests and the audio plumbing. The previous staged plan for downloading Whisper, Qwen and Kokoro was removed. A local microphone/speaker demo exists and the owner has run the headphone conversation on real hardware; speaker mode and reconnect still need a real-device check.

## Setup
```bash
python3.13 -m venv .venv                       # system python3 is 3.9.6; 3.13 comes from Homebrew
.venv/bin/pip install -e ".[dev,live,audio]"   # dev tools, the google-genai SDK and sounddevice (no models)
.venv/bin/python -m pytest -q                  # offline: no network, no key, no models
.venv/bin/ruff check src tests
.venv/bin/mypy src
```
## Gemini key (only for the opt-in real-API checks)
Create a key in Google AI Studio and keep it in a git-ignored `.env`. The code reads environment variables only: `set -a; source .env; set +a`. Never paste the key into chat, code, logs or commits. Required for real runs: `GEMINI_API_KEY`, `TARA_LIVE_MODEL` (pick an id from the models page; ids change).
## Real-session checks
```bash
.venv/bin/python -m tara.voice.smoke                                   # real call, prints measurements only
TARA_LIVE_TEST=1 .venv/bin/python -m pytest -m live_api tests/live -q  # same check as an opt-in test
```
## Local voice demo (microphone in, voice out)
```bash
set -a; source .env; set +a
.venv/bin/python -m tara.voice.demo --one-on-one            # headphones: replies to any speech
.venv/bin/python -m tara.voice.demo --one-on-one --speaker  # speakers: no echo, barge-in disabled
.venv/bin/python -m tara.voice.demo                         # meeting rule: "Tara, ..."
.venv/bin/python -m tara.voice.demo --profile professional
```
Use **headphones** for full duplex. Without them Tara's reply is captured by the microphone and sent back to the model as if it were your speech; `--speaker` avoids that by sending silence while Tara speaks, at the cost of voice barge-in (press `Ctrl+C` to stop). The demo replies in English, uses the default devices, keeps the normal participation gate, and does not touch Google Meet. `--one-on-one` uses the existing `proactive_allowed` policy for a single participant; it is only valid outside interview modes. Without it, say “Tara, …” to address her. Output is resampled from the model's 24 kHz to the output device's default rate. The owner has run the headphone conversation on real hardware; speaker mode and reconnect still need a real-device check. `sounddevice` is an optional `audio` extra; the adapter itself raises a clear `JoinError` if the mic or speakers cannot be opened. Add `--debug` to print non-content counters (captured chunks, input transcripts, response audio, gate outcomes, send/playback errors) without printing speech or transcripts; the same summary prints when the demo exits.
## Not available yet
Google Meet transport (see [research/PHASE0_MEET_SPIKE.md](../google-meet/PHASE0_MEET_SPIKE.md)). Optional dormant local-model adapters exist in `src/tara/providers/` but nothing installs or starts them; do not download models unless the owner decides to revive that path.
## Common errors
`config errors: GEMINI_API_KEY ...` means the environment is not loaded; `ProviderTimeout: live connect timed out` means network or model id; a `Fatal` provider error with code 401/403/404 means a bad key or unknown model (not retried). `audio error: could not open the microphone/speakers` means the default device is missing, busy, or does not support the requested rate (16 kHz input; output uses the device default rate, resampled from 24 kHz).

## Persistent memory (optional, PostgreSQL)

Memory is off by default and adds no tools when off. To enable it locally:

```bash
createdb tara_memory
.venv/bin/python -m pip install -e '.[memory]'
TARA_MEMORY_DATABASE_URL=postgresql:///tara_memory .venv/bin/python -m tara.memory.migrate
# then add to .env (placeholders in .env.example):
# TARA_MEMORY_ENABLED=1
# TARA_MEMORY_DATABASE_URL=postgresql:///tara_memory
```

Run the demo normally; add `--debug` to see `memory_writes`, `memory_recalls`, `memory_write_failures` and `memory_startup_failures`. Say “Remember this …” to save, “What did I tell you about …?” to recall, and “Forget that …” to delete. The model has to call the memory tools; only fake-backend tool tests have run, so a real Gemini conversation is still required to confirm behaviour.

PostgreSQL is the only required service. Redis is installed on this machine but Tara does not use it: the app is single-process, the startup query is bounded, and no measured need exists yet. Do not add it to a code path without recording the measurement in an ADR.

PostgreSQL integration tests create a throwaway schema and drop it:

```bash
TARA_TEST_DATABASE_URL=postgresql:///tara_memory \
  .venv/bin/python -m pytest -m db tests/memory/test_memory_postgres.py -q
```

No memory text, query or database URL is printed by the demo. Full operations and failure modes: [MEMORY_OPERATIONS](../memory/MEMORY_OPERATIONS.md).

## Headphones or speakers (demo)
`python -m tara.voice.demo` picks automatically (`--output auto`, the default) and prints what it decided. Built-in speakers and anything it cannot identify use half-duplex (mic muted while Tara speaks, so no voice interruption); headphone-like devices keep full voice interruption. Override with `--headphones` or `--speaker`. Detection runs once at start and wired-headphone detection is unverified; see `src/tara/adapters/output_detect.py`.
