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

## Unified runtime sources (accessed 2026-10-20)
| # | Source | Established |
|---|---|---|
| U1 | https://huggingface.co/Qwen/Qwen3-0.6B | Apache-2.0; 0.6B params (0.44B non-embedding); 32,768 context; `enable_thinking` default true, False disables; MLX-LM and llama.cpp listed; recommended sampling: non-thinking temp 0.7, top_p 0.8, top_k 20 |
| U2 | https://github.com/ml-explore/mlx-lm | MIT; `pip install mlx-lm`; `load`, `generate`, `stream_generate`; Apple silicon; Qwen3 not named in the README |
| U3 | https://huggingface.co/mlx-community/Qwen3-0.6B-4bit/tree/main | 11 files, 351 MB total, `model.safetensors` 335 MB |
| U4 | https://ai.google.dev/api/generate-content | `POST .../v1beta/models/{model}:generateContent`; examples pass the key as a `key` query parameter (header name not stated on the fetched text; we use `x-goog-api-key`, unverified); snake_case generation config in examples |
| U5 | https://huggingface.co/mlx-community/whisper-small-mlx/tree/main, https://huggingface.co/hexgrad/Kokoro-82M/tree/main, https://pypi.org/project/silero-vad/ | sizes 481 MB, 363 MB (Kokoro weights are a pickle `.pth`), silero-vad 6.2.3 needs torch |

## Gemini Live sources (accessed 2026-10-20; summaries by a fetch tool)
| # | Source | Established |
|---|---|---|
| L1 | https://ai.google.dev/gemini-api/docs/live | Stateful WSS; server-to-server vs client-to-server; PCM16 16 kHz in, 24 kHz out; barge-in, tools, transcription, proactive audio listed; partner integrations (LiveKit, Pipecat, Agora...) |
| L2 | https://ai.google.dev/gemini-api/docs/live-guide | Python calls (`send_realtime_input`, `audio_stream_end`, `activity_start/end`), mime `audio/pcm;rate=16000`, transcription config, VAD config fields, `interrupted`, response modalities, voice config, proactive audio (v1beta) |
| L3 | https://ai.google.dev/gemini-api/docs/live-session | 15 min audio-only without compression, 2 min with video; connection about 10 min; GoAway `time_left`; session resumption handles valid 2 h; context window compression |
| L4 | https://ai.google.dev/gemini-api/docs/live-tools | function declarations, `send_tool_response`, manual handling, NON_BLOCKING + scheduling, not on 3.1 Flash Live; table/sample model-name mismatch |
| L5 | https://ai.google.dev/gemini-api/docs/ephemeral-tokens | Live-only, `v1beta`, `uses`, `expire_time`, `live_connect_constraints`, not for backend connections |
| L6 | https://ai.google.dev/api/live | message types, ServerContent fields, ActivityHandling semantics, TurnCoverage values |
| L7 | https://ai.google.dev/gemini-api/docs/models | Live model ids and status (see GEMINI_LIVE.md) |
| L8 | https://ai.google.dev/gemini-api/docs/pricing | Live pricing (paid) and free tier; free-tier data may improve Google products |
| SDK | google-genai 2.31.0 (Apache-2.0), installed in `.venv` and inspected offline: `live.AsyncSession` methods, `types.LiveConnectConfig` fields, message types, `receive()` source | no network, no key used |

## Conversational behaviour sources (accessed 2026-10-21; summaries by a fetch tool)
| # | Source | Established |
|---|---|---|
| L9 | https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live | 3.8 Live: limits, interleaved reasoning, async function calling default, client content all session, proactive audio always on (false errors), affective dialogue removed, structured output unsupported |
| L10 | https://ai.google.dev/gemini-api/docs/speech-generation | TTS style field, `<laugh>`/`<sigh>`/pause tags, 30 prebuilt voices; states TTS is distinct from the Live API and does not say these controls apply to Live |
| L2 (re-read) | https://ai.google.dev/gemini-api/docs/live-guide | affective dialog and proactive audio sections; no coverage of tone, accent or non-verbal control for Live; `send_client_content` supported through the session |
| SDK | google-genai 2.31.0 `LiveConnectConfig` field help: `enable_affective_dialog` "detect emotions and adapt" (not valid on 3.8 Live per L9), `proactivity`, `thinking_config` | local introspection |
