# Configuration

Status: Proposed. Environment variables (placeholders only in `.env.example`):

| Var | Meaning |
|---|---|
| `GEMINI_API_KEY` | secret, never logged |
| `TARA_LLM_PROVIDER` / `TARA_LLM_MODEL` | provider and model id (verify at ai.google.dev/gemini-api/docs/models) |
| `TARA_STT_BACKEND` / `TARA_STT_MODEL` | e.g. `mlx_whisper` / size |
| `TARA_TTS_BACKEND` / `TARA_TTS_VOICE` | `kokoro` / voice id |
| `TARA_MODELS_DIR` | model cache directory |
| `TARA_MEETING_ADAPTER` | `local` \| `browser` \| `vendor` \| `media_api` |
| `TARA_DATA_DIR`, `TARA_RETAIN_*` | storage and retention |
| `PYTORCH_ENABLE_MPS_FALLBACK=1` | Kokoro on Apple Silicon (S8) |
Thresholds (attribution, end-of-turn, proactive) are config with documented "calibrate" notes, not constants. Meeting behavior lives in `MeetingConfig` ([MEETING_CONTEXT_MODEL.md](MEETING_CONTEXT_MODEL.md)).

## Gemini Live (primary voice engine, 2026-10-20)
All read by `RuntimeSettings.from_env` (`src/tara/settings.py`); see [GEMINI_LIVE](GEMINI_LIVE.md) for meanings. `GEMINI_API_KEY` and `TARA_LIVE_MODEL` are required when `TARA_LIVE_ENABLED=1`; there is deliberately no default model id. The `TARA_STT_*`, `TARA_TTS_*`, `TARA_LOCAL_LLM_*` and `TARA_MODELS_DIR` variables belong to the dormant local path and can stay unset.
