# Tara Documentation

Source of truth for product intent: [`../initial.md`](../initial.md). Most of the backend is implemented and tested offline; every doc says its own status. One real one-turn Gemini Live smoke test has run (text in, audio out); a local mic/speaker demo adapter exists and the owner has run a headphone conversation, nothing has run with Google Meet, and live behaviour, interruption and reconnect remain unverified. Read-only orchestration tools (time, location, weather, public pages, bounded research) are implemented; Open-Meteo and countries.dev were checked live, Brave search was not (no key). Research date: 2026-10-19; orchestration added 2026-10-20.

## Product
[PRODUCT_VISION](PRODUCT_VISION.md) · [PRODUCT_REQUIREMENTS](PRODUCT_REQUIREMENTS.md) · [GLOSSARY](GLOSSARY.md)
## Architecture & design
[SYSTEM_ARCHITECTURE](SYSTEM_ARCHITECTURE.md) · [COMPONENTS_AND_RESPONSIBILITIES](COMPONENTS_AND_RESPONSIBILITIES.md) · [MEETING_CONTEXT_MODEL](MEETING_CONTEXT_MODEL.md) · [MEETING_RUNTIME](MEETING_RUNTIME.md) · [TURN_TAKING_AND_INTERRUPTION](TURN_TAKING_AND_INTERRUPTION.md) · [PARTICIPANT_IDENTITY](PARTICIPANT_IDENTITY.md) · [SPEAKER_DIARIZATION](SPEAKER_DIARIZATION.md) · [CONVERSATION_ENGINE](CONVERSATION_ENGINE.md) · [AUDIO_PIPELINE](AUDIO_PIPELINE.md) · [AI_PROVIDER_INTEGRATION](AI_PROVIDER_INTEGRATION.md) · [MEETING_PLATFORM_INTEGRATION](MEETING_PLATFORM_INTEGRATION.md) · [API_AND_EVENT_CONTRACTS](API_AND_EVENT_CONTRACTS.md) · [DATA_MODEL](DATA_MODEL.md) · [CONFIGURATION](CONFIGURATION.md)
## Research
[GOOGLE_MEET_RESEARCH](GOOGLE_MEET_RESEARCH.md) · [SPEECH_RECOGNITION_RESEARCH](SPEECH_RECOGNITION_RESEARCH.md) · [SPEAKER_IDENTIFICATION_RESEARCH](SPEAKER_IDENTIFICATION_RESEARCH.md) · [VAD_AND_TURN_DETECTION_RESEARCH](VAD_AND_TURN_DETECTION_RESEARCH.md) · [GEMINI_RESEARCH](GEMINI_RESEARCH.md) · [KOKORO_RESEARCH](KOKORO_RESEARCH.md) · [REAL_TIME_ARCHITECTURE_RESEARCH](REAL_TIME_ARCHITECTURE_RESEARCH.md) · [AVATAR_RESEARCH](AVATAR_RESEARCH.md) · [research/](research/README.md) (sources, comparisons, live-voice reference, open questions)
## Voice engine (primary)
[GEMINI_LIVE](GEMINI_LIVE.md): Gemini Live architecture, verified facts, interruption, run steps, status · [ORCHESTRATION](ORCHESTRATION.md): time zones, location, weather, search, pages, bounded research, background tasks · [CONVERSATIONAL_BEHAVIOUR](CONVERSATIONAL_BEHAVIOUR.md): personality, profiles, meeting kinds, humour, expression events, floor etiquette · [MEETING_CONTROLS](MEETING_CONTROLS.md): spoken commands, permissions, platform limits, notes
## Runtime foundation (optional local path)
[RUNTIME_FOUNDATION](RUNTIME_FOUNDATION.md) (implemented against fakes: routing, state, lifecycle, tools, context assembly, metrics)
## Memory and RAG
[MEMORY_AND_RAG](MEMORY_AND_RAG.md) · [MEMORY_OPERATIONS](MEMORY_OPERATIONS.md) · [CONTEXT_ASSEMBLY](CONTEXT_ASSEMBLY.md) · [MEMORY_DATA_MODEL](MEMORY_DATA_MODEL.md) · [MEMORY_AND_RETRIEVAL_SECURITY](MEMORY_AND_RETRIEVAL_SECURITY.md) · [RAG_AND_MEMORY_EVALUATION](RAG_AND_MEMORY_EVALUATION.md) · [research/RAG_TECHNOLOGY_EVALUATION](research/RAG_TECHNOLOGY_EVALUATION.md)
## Operations & quality
[SECURITY_AND_PRIVACY](SECURITY_AND_PRIVACY.md) · [FAILURE_MODES](FAILURE_MODES.md) · [TESTING_STRATEGY](TESTING_STRATEGY.md) · [OBSERVABILITY](OBSERVABILITY.md) · [LOCAL_DEVELOPMENT](LOCAL_DEVELOPMENT.md)
## Planning
[CHANGELOG](../CHANGELOG.md) · [IMPLEMENTATION_ROADMAP](IMPLEMENTATION_ROADMAP.md) · [ARCHITECTURAL_DECISIONS](ARCHITECTURAL_DECISIONS.md) · [adr/](adr/README.md)

Status labels: Implemented · Partially implemented · Designed not implemented · Proposed · Researched not selected · Unverified · Blocked · Future.
