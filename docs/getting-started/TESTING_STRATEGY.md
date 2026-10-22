# Testing Strategy

Tiers (separate markers): `offline` (default, no creds/models), `local_model` (needs downloaded models), `live_api` (Gemini key), `real_meeting` (manual/E2E).

## Unit (offline)
Config validation (each problem class), state transitions (table-driven), participation decisions (matrix), speaker mapping & label-swap, permission checks, response validation (schema, grounding, forbidden commitments), event dedupe, epoch/staleness.

## Integration
Gemini provider (live_api; plus fake for malformed/timeout), STT/TTS (local_model), audio processing (synthetic PCM), persistence, tool executor permission paths. Meeting adapter: contract tests for fake; real adapter tests only in `real_meeting`.

## Simulation (scripted transcripts + attribution into runtime with fake providers)
Direct address · talk-about-Tara · two humans debate · one-candidate interview · group interview (A answers, B interrupts, C adds) · pitch with missing feature · barge-in · takeover mid-speech · unknown speaker · conflicting instructions · wrong transcript ("Sara" vs "Tara") · speaker change mid-answer · provider timeout · disconnect during playback · delayed response becomes stale.
Key assertions: A's evidence never in B's report; no speech after takeover; no second response for one trigger; no unsupported product claim.

## End-to-end (real meeting; unavailable until Phase 4)
Metrics, baselines first, targets after benchmarking (no invented numbers): end-of-utterance→first audio, STT latency, attribution accuracy, **false attribution rate**, false activation, missed direct address, interruption response time, stale-response delivery rate (target 0 by construction), unauthorized tool attempts blocked.
A mocked adapter does not prove Meet integration.

Rules: never claim pass without running; no disabling failing tests.
