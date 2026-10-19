# Speech Stack Comparison (STT, VAD, TTS)

Accessed 2026-10-19; see [SOURCES.md](SOURCES.md). "Unknown" = not established by a fetched source.

## STT
| Criterion | faster-whisper (S9) | mlx-whisper (S10) | whisper.cpp | Implication |
|---|---|---|---|---|
| Local inference | Yes | Yes | Unknown; requires verification (not fetched) | All local-capable |
| Apple Silicon GPU | Page does not mention Metal; CPU int8 documented | MLX is Apple's framework; page does not state perf | Unknown; requires verification | On M4 Pro, benchmark mlx-whisper first |
| Streaming | No native API; iterate segments over completed audio | No streaming API described | Unknown | **Segment-based**: transcribe each VAD-closed segment; partials are a Phase 2+ experiment |
| Timestamps | Segment/word | `word_timestamps=True` | Unknown | Needed for alignment with speaker turns |
| License | MIT | Not stated on page | Unknown | Verify before distribution |
| Complexity | Low | Low (needs ffmpeg) | Medium | |

Whisper by itself provides **no speaker identity** ([SPEAKER_IDENTIFICATION_RESEARCH.md](../SPEAKER_IDENTIFICATION_RESEARCH.md)).
Recommendation: `SpeechRecognizer` interface + mlx-whisper first, faster-whisper CPU as fallback. Confidence: low-medium until benchmarked (Q3).

## VAD
Silero VAD (S11): MIT per page, 16 kHz, ≥30 ms chunks, <1 ms/chunk CPU, ~2 MB. Fits always-on endpointing. Streaming usage must be built by feeding chunks; the fetched page only showed file-level examples. Alternatives (WebRTC VAD, pyannote VAD): unverified.

## TTS
Kokoro (S8): 82M params, Apache-2.0, 24 kHz, per-segment generator, `espeak-ng` dependency, MPS fallback flag on M-series. It yields audio **per text segment** (e.g. per sentence via `split_pattern`), not per token; so latency to first audio = LLM time to first complete sentence + Kokoro first-segment time. Cancellation granularity = segment boundary unless generation runs in a cancellable worker. Alternatives: cloud TTS (Gemini TTS/Live audio out) — not researched in depth.
