# Diarization & Speaker Attribution Options

| Approach | Evidence | Strength | Weakness | Use |
|---|---|---|---|---|
| Participant-level audio from platform (Media API CSRC S1; vendor per-participant audio S14) | Official/vendor docs | Identity = platform participant, no guessing | Availability blocked/paid (see [GOOGLE_MEET_OPTIONS.md](../google-meet/GOOGLE_MEET_OPTIONS.md)) | **Preferred when available** |
| Active-speaker metadata (DOM indicator / vendor speaker timeline) | S14 (vendor); DOM unverified | Cheap name hints | Timing lag, wrong during overlap | Evidence, not proof |
| Diarization on mixed audio (pyannote community-1, S12) | MIT code, gated weights; real-time & Apple Silicon undocumented | Works without platform help | Labels are anonymous and unstable across windows; weak on short utterances/overlap | Offline relabel + low-weight live hint |
| Voice embeddings vs enrolled participants | pyannote lists embeddings (S12) | Can map label→person if enrolled | Needs consent + enrollment audio; biometric privacy | Optional, opt-in only |
| Self-introduction / confirmation ("Hi, I'm Alex") | Design | Strong, human-confirmed | Requires conversation step | Used in interviews at start |

**PoC strategy (proposed):** (1) fuse platform speaker hints + diarization label + introduction into a per-utterance `SpeakerAttribution{participant_id|null, confidence, evidence[]}`; (2) below threshold → `unknown`, never guess. Details: [PARTICIPANT_IDENTITY.md](PARTICIPANT_IDENTITY.md). Thresholds: to be calibrated on recorded data (not invented here).
