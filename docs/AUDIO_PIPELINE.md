# Audio Pipeline

**Status: Designed, not implemented.** Numbers marked **[benchmark]** are intentionally unspecified.

| # | Stage | Notes |
|---|---|---|
| 1 | Acquire | Adapter yields frames `{stream_id, pcm, capture_ts}`. Meet Media API example uses Opus 48 kHz stereo (S2); browser capture formats unverified |
| 2 | Validate | Reject wrong dtype/length; log format drift |
| 3 | Resample | To 16 kHz mono PCM16 for VAD/STT (Silero supports 8/16 kHz, S11; Gemini Live input is 16 kHz PCM16, S6) |
| 4 | Buffer | Ring buffer per stream, **bounded** (seconds **[benchmark]**); on overflow drop oldest non-speech first and emit `audio.overflow` metric |
| 5 | VAD | ≥30 ms chunks (S11); hysteresis; pre-roll so onsets aren't clipped **[benchmark]** |
| 6 | Segment | Close on end-of-speech + hangover **[benchmark]**; cap segment length |
| 7 | Attribute | Hints + diarizer → `SpeakerAttribution` |
| 8 | Transcribe | Segment-based STT in worker; partial results optional later |
| 9 | Emit | `transcript.final{utterance_id,...}`; dedupe by `utterance_id` |
| 10 | Context update | Runtime appends transcript + attribution |
| 11 | End-of-turn estimate | See [VAD_AND_TURN_DETECTION_RESEARCH.md](VAD_AND_TURN_DETECTION_RESEARCH.md) |
| 12 | Generate/validate | Conversation engine; staleness check |
| 13 | Synthesize | Kokoro 24 kHz (S8), per sentence segments; cancellable worker |
| 14 | Output | Resample to platform format; chunked push; playback tracker reports `delivered_ms` |
| 15 | Cancel | Flush TTS queue + output buffer; ack |
| 16 | Metrics | Per-stage timestamps (below) |

All timestamps from one monotonic clock plus capture-time offset per stream.

## Preventing known hazards
- **Feedback / self-transcription:** (a) prefer per-participant streams so Tara's own track is excluded by identity; (b) with mixed audio, keep an *output reference* and suppress VAD/STT during Tara playback windows only if barge-in is detected by energy exceeding reference (needs echo cancellation, **unverified**, Q7); (c) drop transcripts whose text matches recently synthesized text with timing overlap; (d) in browser mode, route TTS to a virtual mic and capture the meeting tab output, not the mic.
- **Duplicate transcripts:** `(stream_id, segment_start_ms)` idempotency key.
- **Stale responses:** epoch + age check at approval and again at playback start.
- **Excess buffering/memory:** bounded rings, bounded queues, backpressure drops with metrics; segment length cap.
- **Playback after takeover:** takeover bumps epoch; output worker checks epoch per chunk.
- **Wrong speaker:** attribution carried per segment; never merged across attribution changes.

## Metrics per utterance
`t_speech_end`, `t_vad_close`, `t_stt_done`, `t_llm_first_token`, `t_llm_done`, `t_tts_first_chunk`, `t_playback_start`, derived latencies. Target values set after measurement.
