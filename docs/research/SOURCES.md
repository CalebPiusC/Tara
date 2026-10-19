# Research Source Index

All sources accessed **2026-10-19** via page fetch, summarized by a fetch tool (not read in full by a human). Treat each row as "what the page said on that date"; re-verify before relying on it for a release. Nothing here was verified by running code.

| # | Source | Established |
|---|---|---|
| S1 | https://developers.google.com/workspace/meet/media-api/guides/overview | Meet Media API is **Developer Preview**, **"no longer accepting new signups"**. WebRTC/SRTP, receive audio+video+participant metadata. Virtual streams from an SFU; RTP CSRC identifies source participant. Needs consent (host Calendar setting + admin setting), blocked for encrypted/watermarked meetings. Publishing media into the meeting is **not described**. |
| S2 | https://developers.google.com/workspace/meet/media-api/guides/concepts | Offer must be receive-only; Opus `48000/2` in example; no eligibility list. |
| S3 | https://developers.google.com/workspace/meet/api/guides/overview | Meet REST API: spaces, conference records, participants/sessions, members, artifacts (recordings, transcripts, smart notes). Transcript entries kept 30 days. **No live audio.** Events via Workspace Events API. Does not state bots can join. |
| S4 | https://developers.google.com/workspace/meet/add-ons/guides/overview | Add-ons SDK embeds a web app in the Meet UI (side panel/main stage). Page does not describe audio access or injection. |
| S5 | https://ai.google.dev/gemini-api/docs/models | Flash ids on 2026-10-19: `gemini-3.8-flash` (stable), `gemini-3.6-flash` (stable), `gemini-3-flash-preview`; Live: `gemini-3.8-live` (stable). No context sizes stated for Flash. |
| S6 | https://ai.google.dev/gemini-api/docs/live | Live API: stateful WSS; input PCM16 16 kHz, output PCM16 24 kHz; barge-in; function calling; ephemeral tokens recommended for client-side. |
| S7 | https://ai.google.dev/gemini-api/docs/structured-output | JSON-schema subset, streamed partial JSON; docs warn output can be schema-valid yet semantically wrong. |
| S8 | https://github.com/hexgrad/kokoro | Kokoro-82M, Apache-2.0 weights, `pip install kokoro` + `espeak-ng`, generator yields per text segment, 24 kHz, `PYTORCH_ENABLE_MPS_FALLBACK=1` on M-series. |
| S9 | https://github.com/SYSTRAN/faster-whisper | MIT. CUDA or CPU (int8). **Apple Metal not mentioned.** No native streaming API. Uses Silero VAD filter. |
| S10 | https://github.com/ml-explore/mlx-examples/tree/main/whisper | `mlx-whisper` package: `pip install mlx-whisper`, needs ffmpeg, word timestamps, MLX-community checkpoints. License not stated on page. |
| S11 | https://github.com/snakers4/silero-vad | MIT (page badge inconsistent), 8k/16k Hz, ≥30 ms chunks, <1 ms/chunk CPU, ~2 MB. Examples are file-based. |
| S12 | https://github.com/pyannote/pyannote-audio | MIT code; `community-1` pipeline is gated on Hugging Face (token + accept conditions). Streaming/real-time and Apple Silicon not documented. |
| S13 | https://developers.openai.com/api/docs/guides/realtime-conversations | Reference for how a production live-voice stack models turns: server VAD events, `speech_started` cancels response, client truncates to played audio (`conversation.item.truncate`), semantic VAD, VAD can be decoupled from auto-response, 60-min session cap. |
| S14 | https://docs.recall.ai/docs/bot-overview | Third-party bot API: Meet supported, per-participant audio real-time, speaker timeline, participant events, Output Media to play audio/avatar. Vendor claims, **unverified by us**; pricing not stated. |

## Not verified (no source fetched)
whisper.cpp / Core ML performance on M4 Pro; pyannote on MPS; Kokoro latency on M4 Pro; WebRTC echo/AEC behavior in headless Chrome; Google ToS stance on automated Meet participants; Gemini rate limits and data-retention terms (pages not fetched). See [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).

## Memory and RAG sources (accessed 2026-10-19)
R1-R7 are listed with what each established in [RAG_TECHNOLOGY_EVALUATION.md](RAG_TECHNOLOGY_EVALUATION.md): pgvector README, PostgreSQL full-text ranking docs, Gemini embeddings docs, bge-small-en-v1.5 and bge-reranker-base model cards, OWASP LLM01, Ragas metrics list. Not fetched: Postgres Row-Level Security docs, RRF paper, any benchmark.
