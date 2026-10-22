# Engine

The engine that runs a conversation: Gemini Live sessions, audio in and out, what the model is given each turn.

- [GEMINI_LIVE](GEMINI_LIVE.md): The primary voice engine: architecture, verified facts, interruption, reconnect, run steps, status
- [AUDIO_PIPELINE](AUDIO_PIPELINE.md): Audio formats, framing and playback
- [CONTEXT_ASSEMBLY](CONTEXT_ASSEMBLY.md): What context Gemini receives and how it is bounded
- [RUNTIME_FOUNDATION](RUNTIME_FOUNDATION.md): The unified runtime (routing, state, lifecycle, tools, metrics); the local-model path is dormant
- [CONVERSATION_ENGINE](CONVERSATION_ENGINE.md): Prompt-to-proposal engine for the cascaded path (designed, not implemented)
- [LIVE_VOICE_REFERENCE](LIVE_VOICE_REFERENCE.md): Lessons from production live-voice sessions
