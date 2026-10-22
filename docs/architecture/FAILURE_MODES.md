# Failure Modes and Recovery

Default: fail safe = stay silent, keep listening if possible, log, never pretend.

| Failure | Detection | Immediate behavior | Recovery | Continue? |
|---|---|---|---|---|
| Meet connect fails | adapter error / timeout | no speech | bounded backoff, then STOPPED with report of reason | pause |
| Meeting ended unexpectedly | lifecycle event | flush state, finalize partial report | none | stop |
| Mic/audio input denied or missing | startup probe | refuse to join | operator fixes permission | stop |
| Audio output unavailable | playback error | mark utterance `failed`; no retry loop | switch device or observe-only | continue silent |
| VAD misses speech | STT empty segments, human "can you hear" | none auto | lower threshold (config) | continue |
| Transcription fails | worker exception | skip segment, mark gap | retry once, then degrade | continue, flagged gap in report |
| Speaker uncertain | confidence < threshold | label unknown; ask for name if interview | corrections | continue; no candidate-specific evidence |
| Gemini unavailable/timeout | timeout/5xx | no response generated; optional fixed "one moment" only if configured | bounded retry; otherwise stay silent | continue listening |
| Gemini malformed output | schema validation | discard; one retry | else silent | continue |
| Kokoro fails | worker error | text not spoken | restart worker once | observe-only fallback |
| Model load fails / OOM | startup/exception | fail start with clear error | smaller model via config | stop |
| Response too late | epoch/age check | drop | n/a | continue |
| Participant interrupts | VAD during SPEAKING | INTERRUPTED | re-evaluate | continue |
| Human takeover | phrase/API | YIELDING_CONTROL | explicit release | continue silent |
| Duplicate events | id dedupe | ignore | n/a | continue |
| Reconnect | adapter event | epoch++, undelivered flagged | resume LISTENING | continue |
| Config vs runtime conflict (e.g. silent policy but tool wants speech) | runtime invariant check | runtime wins | log | continue |
| Unauthorized tool action | permission check | refuse, audit log | notify organizer | continue |
| Knowledge source unavailable | provider error | answer "I can't verify that right now"; no guessing | retry | continue |
| Out-of-role question | scope check | brief polite decline or defer to human | n/a | continue |

Logging: event id, state, error class; no transcript/audio. User-visible: only configured fallback phrases; otherwise silence plus post-meeting notes of gaps.
