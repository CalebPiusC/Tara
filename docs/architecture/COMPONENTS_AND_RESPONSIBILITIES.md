# Components and Responsibilities

Contract signatures: [API_AND_EVENT_CONTRACTS.md](API_AND_EVENT_CONTRACTS.md). All **Designed, not implemented**.

| Component | Responsibility | Explicitly does NOT |
|---|---|---|
| MeetingAdapter | Join/leave, emit audio+participant+lifecycle events, accept outgoing audio, report delivered position | Interpret speech, decide to speak |
| AudioInput / AudioOutput | Format validation, resample, bounded buffers, playback with position tracking | Know about participants |
| VoiceActivityDetector | Emit speech start/end with timestamps | Decide end of *turn* |
| SpeechRecognizer | Text + word/segment timestamps for a segment | Identify people |
| SpeakerAttributor | Fuse hints → `SpeakerAttribution` with confidence | Name a person from a diarization label alone |
| ContextEngine | Natural language → validated `MeetingConfig`; surface problems | Grant permissions from prose |
| ParticipationEngine | Deterministic gate + semantic classification inputs → `ParticipationDecision` | Generate speech |
| ConversationEngine | Build prompts from state, call LLM, return validated `ResponseProposal` | Speak, call tools directly |
| InterviewEngine | Question plan, follow-up need, rubric evidence store | Make hiring decisions |
| KnowledgeProvider | Return approved passages with ids | Invent facts |
| ToolExecutor | Run declared tools after permission check, audit | Run model-supplied code |
| LanguageModelProvider | Timeouts, retries, schema validation, model id from config | Business policy |
| SpeechSynthesizer | Text → PCM chunks, cancellable | Decide what to say |
| MeetingRuntime | State machine, epoch, approvals, stale-checks, event bus, takeover | Provider-specific code |
| ReportGenerator | Post-meeting reports from stored evidence | Fabricate evidence |
| AvatarProvider (future) | Consume audio/timing/state | Required for core operation |
