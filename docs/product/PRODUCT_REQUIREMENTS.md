# Product Requirements

IDs are stable; tests reference them. Status of all: **Designed, not implemented**.

## Functional
| ID | Requirement |
|---|---|
| FR-1 | Accept organizer context (free text + structured fields) and produce a validated `MeetingConfig` before the meeting starts |
| FR-2 | Reject/flag ambiguous, contradictory, impossible, or unauthorized instructions before start |
| FR-3 | Join a meeting through a `MeetingAdapter` and report lifecycle events |
| FR-4 | Transcribe participant speech with timestamps and per-utterance speaker attribution including `unknown` |
| FR-5 | Decide per turn: listen, respond, ask clarification, or stay silent, via ParticipationEngine policy |
| FR-6 | Detect direct address without relying only on the name token |
| FR-7 | Speak via TTS only after runtime approval, and only if still fresh |
| FR-8 | Stop speech on barge-in or human takeover within a measured bound |
| FR-9 | Interview mode: question plan, follow-ups from actual answers, rubric evidence per candidate |
| FR-10 | Never assign candidate-specific evidence to a low-confidence speaker |
| FR-11 | Pitch mode: answer only from approved knowledge; admit unknowns; no unauthorized commitments |
| FR-12 | Produce post-meeting report (interview / decisions+actions / pitch) with evidence links |
| FR-13 | Execute tools only if declared in config **and** allowed by app policy |
| FR-14 | Swap LLM/STT/TTS/meeting platform via config and adapters |

## Non-functional
| ID | Requirement |
|---|---|
| NFR-1 | Latency measured end to end; targets set only after benchmarking ([TESTING_STRATEGY.md](../getting-started/TESTING_STRATEGY.md)) |
| NFR-2 | Bounded queues everywhere; memory does not grow with meeting length |
| NFR-3 | Secrets only from environment; never logged |
| NFR-4 | Raw audio not persisted by default; retention configurable and documented |
| NFR-5 | Unit tests run offline, no model downloads, no credentials |
| NFR-6 | Participant disclosure of AI presence is a start-up precondition (configurable text, not skippable silently) |
| NFR-7 | Every external provider call has timeout and bounded retry |

Open questions: [research/OPEN_QUESTIONS.md](../research/OPEN_QUESTIONS.md).
