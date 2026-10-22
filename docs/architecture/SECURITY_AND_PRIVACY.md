# Security and Privacy

**Status: Designed, not implemented.** Not legal advice; consent and employment-law requirements vary by jurisdiction (Q8).

| Asset | Risk | Control |
|---|---|---|
| Meeting audio | Capture without consent | Disclosure at start (`privacy.disclosure_text`) is a precondition; organizer attests consent; observe-only still discloses |
| Raw audio | Indefinite storage | Not persisted by default; if enabled, explicit retention + deletion job |
| Transcripts | Sensitive, sent to Gemini | `send_transcript_to_llm` flag; minimize context window; provider terms review (Q5) |
| Participant identity | Misattribution, biometric voiceprints | Voice enrollment opt-in only; `unknown` default |
| Interview evaluations | Employment decisions, bias | Decision support only; human review default; evidence links; no auto reject |
| API keys | Leakage | Env vars/secret store; `.env` git-ignored; `.env.example` has placeholders; never logged; prefer ephemeral tokens for any client-side use (S6) |
| Logs | PII | Structured logs with ids, not text; transcript text only at debug and off by default |
| Knowledge base | Poisoned/unapproved docs | `approved` flag; provenance ids |
| Tool execution | Prompt injection from meeting speech | Tools allow-listed in code; per-tool permission + optional human approval; arguments schema-validated; transcript is data not instructions |
| Control API | Unauthorized takeover/stop | Auth token on local API; bind to localhost |

Access control: reports readable only by organizer; deletion API removes transcript, evidence, reports. Right-to-delete and retention windows are config. Participants who object → `silent/observe` or leave (policy TBD).
