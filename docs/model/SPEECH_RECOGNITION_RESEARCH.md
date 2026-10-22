# Speech Recognition Research

> **Superseded 2026-10-20 as the primary path.** Gemini Live is now the voice engine ([GEMINI_LIVE](../engine/GEMINI_LIVE.md)). This document describes the optional, dormant local-model path: nothing here is downloaded, installed or started.
See [research/SPEECH_STACK_COMPARISON.md](SPEECH_STACK_COMPARISON.md). Whisper-family models transcribe completed segments; "streaming" is built by feeding VAD-closed segments (faster-whisper documents no native streaming, S9). No speaker identity. On M4 Pro start with mlx-whisper, fallback faster-whisper CPU int8; model size by benchmark. Unverified: real-time factor, memory, accuracy on meeting audio and accents. Recommendation confidence: low-medium.
