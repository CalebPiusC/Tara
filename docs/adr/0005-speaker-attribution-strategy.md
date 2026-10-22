# ADR-0005: Speaker attribution strategy
**Status:** Proposed
**Context:** [PARTICIPANT_IDENTITY](../speakers/PARTICIPANT_IDENTITY.md). **Decision:** attribution is evidence-fused, versioned, may be `null`; identity-dependent actions require confidence ≥ configured threshold; prefer per-participant audio. **Alternatives:** trust diarization labels (rejected: unstable). **Consequences:** some utterances unusable for individual reports. **Risks:** threshold miscalibration. **Evidence:** S1, S12, S14. **Validation:** Phase 3 measured false-attribution rate.
