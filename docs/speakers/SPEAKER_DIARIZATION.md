# Speaker Diarization Component

**Status: Researched, not selected. Not implemented.** Evidence and options: [research/DIARIZATION_OPTIONS.md](DIARIZATION_OPTIONS.md); identity semantics: [PARTICIPANT_IDENTITY.md](PARTICIPANT_IDENTITY.md).

- **Purpose:** anonymous speaker-change/cluster labels for mixed audio when the adapter cannot give per-participant audio.
- **Not a purpose:** naming people.
- **Input:** 16 kHz mono PCM with monotonic timestamps. **Output:** `DiarSegment{start_ms,end_ms,label,overlap}`.
- **Candidate:** pyannote `community-1` (MIT code; gated weights, HF token; real-time and Apple Silicon behavior undocumented in fetched page, S12). Run offline/relabel pass; live use unproven (Q6).
- **Skipped entirely** when the adapter provides per-participant streams (then stream id is the evidence).
- **Failure:** diarizer down → attribution falls to hints/unknown; meeting continues; interview individual reports gated.
- **Tests:** label-swap, merge, overlap, short-utterance fixtures.
