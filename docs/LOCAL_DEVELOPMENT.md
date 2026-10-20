# Local Development (Apple Silicon, M4 Pro / 24 GB)

**Updated 2026-10-21: no local model is downloaded or needed.** Gemini Live is the voice engine ([GEMINI_LIVE](GEMINI_LIVE.md)); this machine only runs Python, the tests and the audio plumbing. The previous staged plan for downloading Whisper, Qwen and Kokoro was removed. A local microphone/speaker demo now exists, but it has not yet been run on real hardware in this repo.

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
Google Meet transport (see [research/PHASE0_MEET_SPIKE.md](research/PHASE0_MEET_SPIKE.md)). Optional dormant local-model adapters exist in `src/tara/providers/` but nothing installs or starts them; do not download models unless the owner decides to revive that path.
## Common errors
`config errors: GEMINI_API_KEY ...` means the environment is not loaded; `ProviderTimeout: live connect timed out` means network or model id; a `Fatal` provider error with code 401/403/404 means a bad key or unknown model (not retried). `audio error: could not open the microphone/speakers` means the default device is missing, busy, or does not support the requested rate (16 kHz input; output uses the device default rate, resampled from 24 kHz).
