# Model Selection (proposed, not final)

> **Superseded 2026-10-20 as the primary path.** Gemini Live is now the voice engine ([GEMINI_LIVE](../GEMINI_LIVE.md)). This document describes the optional, dormant local-model path: nothing here is downloaded, installed or started.

| Role | First choice | Fallback | Status | Disk/RAM |
|---|---|---|---|---|
| Reasoning | Gemini Flash, id from config (`TARA_LLM_MODEL`). On 2026-10-19 the models page listed `gemini-3.8-flash` and `gemini-3.6-flash` as stable (S5) | Other listed Flash id; Live API (`gemini-3.8-live`) evaluated for ADR-0002 | Proposed | n/a (cloud) |
| STT | mlx-whisper, size chosen by benchmark | faster-whisper int8 CPU | Proposed | Unknown; requires verification per checkpoint |
| VAD | Silero VAD | — | Proposed | ~2 MB (S11) |
| TTS | Kokoro-82M | cloud TTS | Proposed | 82M params (S8); disk not verified |
| Diarization | none in Phase 2; pyannote community-1 in Phase 3 (gated HF token) | platform participant audio | Proposed | Unknown |

Model ids live in config, never in code ([CONFIGURATION.md](../CONFIGURATION.md)). **No models downloaded during this phase.**
