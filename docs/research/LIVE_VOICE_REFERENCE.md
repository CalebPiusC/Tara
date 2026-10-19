# Lessons from Production Live-Voice Sessions (ChatGPT / OpenAI Realtime)

Source: S13 (OpenAI Realtime conversations guide), accessed 2026-10-19. This documents the *public API's* event model, not ChatGPT's internal implementation, which is not public. Tara does not copy it; it borrows the lessons that fit a meeting.

| Pattern in the reference | What it teaches | How Tara uses it |
|---|---|---|
| Server emits `input_audio_buffer.speech_started` / `speech_stopped` | Turn boundaries are **events**, not inferred from transcript text | Tara's VAD emits `vad.speech_started` / `vad.speech_ended` ([API_AND_EVENT_CONTRACTS.md](../API_AND_EVENT_CONTRACTS.md)) |
| On `speech_started` while the model speaks, the in-flight response is **cancelled** | Barge-in = cancel generation *and* stop playback | `INTERRUPTED` state; cancel LLM + TTS + playback ([TURN_TAKING_AND_INTERRUPTION.md](../TURN_TAKING_AND_INTERRUPTION.md)) |
| Client sends `conversation.item.truncate` with `audio_end_ms`; unplayed transcript is dropped | History must contain only what the listener **actually heard** | Tara tracks `delivered_ms` per utterance and truncates the stored assistant turn |
| `semantic_vad` option | Silence length alone is a weak end-of-turn signal | Combine VAD + transcript completeness + meeting state ([VAD_AND_TURN_DETECTION_RESEARCH.md](../VAD_AND_TURN_DETECTION_RESEARCH.md)) |
| `create_response` / `interrupt_response` can be set false while VAD stays on | **Detecting speech and deciding to reply are separate switches** | Direct mapping to Tara's split: ParticipationEngine decides, model only proposes |
| Push-to-talk disables VAD, uses explicit commit | Manual control is a valid mode | "Moderator-controlled" speaking policy for interviews |
| 60-minute session cap | Providers cap sessions; plan reconnect | Tara stores state locally; model session is disposable |
| WebRTC truncates automatically; WebSocket needs the client to do it | Whoever owns playback owns truth about what was heard | Tara owns playback, so Tara owns truncation |

## What does *not* transfer
- ChatGPT voice is 1:1. Tara faces many speakers, overlap, and unknown addressee. The reference has no concept of "was that said to me?".
- A single speech-to-speech model hides STT/LLM/TTS stages. Tara needs the transcript with speaker attribution as an auditable artifact (interview evidence), so the default is a **cascaded** pipeline. Gemini's Live API (S6) remains an evaluated alternative (ADR-0002, Q9).
