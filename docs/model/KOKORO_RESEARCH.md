# Kokoro Research

> **Superseded 2026-10-20 as the primary path.** Gemini Live is now the voice engine ([GEMINI_LIVE](../engine/GEMINI_LIVE.md)). This document describes the optional, dormant local-model path: nothing here is downloaded, installed or started.
Source S8 (2026-10-19). 82M params, Apache-2.0 weights, `pip install kokoro` plus `espeak-ng`; 24 kHz; `KPipeline(lang_code=...)` generator yields `(graphemes, phonemes, audio)` per text segment (`split_pattern`). On M-series set `PYTORCH_ENABLE_MPS_FALLBACK=1`.
- **Incremental?** Per segment, not per token: send whole sentences. Practical latency = first LLM sentence + first Kokoro segment. Stream LLM output, cut at sentence boundaries, synthesize while the next sentence generates.
- **Interruption:** stop between segments; run TTS in a cancellable worker; discard queued segments.
- **Unverified:** latency on M4 Pro (MPS vs CPU), memory, voice list, naturalness for interview use, disk size. Alternatives: cloud TTS (not researched). Recommendation confidence: medium for feasibility, low for latency until measured.
