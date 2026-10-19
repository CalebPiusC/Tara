# Participant Identity & Speaker Attribution

**Status: Designed, not implemented.**

## Seven separate concepts (never conflate)
| # | Concept | Example | Source |
|---|---|---|---|
| 1 | Meeting participant identity | `participants/abc`, display name "Alex K." | platform |
| 2 | Audio stream identity | track id / RTP CSRC / mixed-stream | adapter |
| 3 | Diarization label | `SPEAKER_00` | diarizer; anonymous, window-local |
| 4 | Real-world identity | "the candidate named Alex Kim" | organizer config / self-intro; **never inferred from voice alone** |
| 5 | Speaker→participant mapping | `SPEAKER_00 → participants/abc` | fusion of evidence |
| 6 | Confidence | 0.0–1.0 + evidence list | computed, calibrated |
| 7 | Corrections | recruiter says "that was Bea, not Alex" | versioned mapping change |

## Data
```json
{"utterance_id":"u_118","start_ms":812300,"end_ms":819900,
 "attribution":{"participant_id":null,"candidates":[{"id":"p_a","score":0.46},{"id":"p_b","score":0.41}],
  "confidence":0.46,"evidence":[{"type":"diarization","label":"SPEAKER_01"},{"type":"active_speaker_hint","name":"Candidate A","lag_ms":300}],
  "mapping_version":7}}
```
`participant_id: null` is a first-class value.

## Why labels lie (examples)
- Window 1 labels Alex=`SPEAKER_00`; after a 10-minute window re-cluster, Alex becomes `SPEAKER_02`. A mapping keyed only on label silently reassigns Alex's speech to whoever is now `SPEAKER_00`.
- Two similar voices merge into one label → one "person" answers both questions.
- A 0.6 s "yeah" cannot support a speaker claim.
- Overlap: B interrupts A; a single-label segment spans both.

## Rules
1. Mapping entries carry `mapping_version`, evidence, and expiry on re-cluster; stale label references are invalid.
2. Short utterances (below a tuned duration) inherit nothing; they are `unknown` unless platform per-participant audio exists.
3. Overlapping spans are split or marked `overlap` and excluded from candidate evidence.
4. Joins/leaves/renames update the registry; renames keep `participant_id`; a returning display name does not imply the same person unless the platform id matches.
5. Unknown speaker → new transient participant `unknown_k`; never auto-merged into a candidate.
6. Corrections: a human correction creates mapping v+1 and **re-labels history through an audit event**; reports regenerate from the corrected evidence, old report is marked superseded.
7. **Low confidence blocks identity-dependent actions:** candidate-specific evidence, naming a candidate, round-robin credit. Tara may ask "Could the speaker say their name?" in interviews.

## Interview safety invariant
`evidence(candidate X)` ⊆ utterances with `participant_id == X ∧ confidence ≥ attribution_min_confidence ∧ ¬overlap`. Enforced in `InterviewEngine`/`ReportGenerator` code, not by prompt.

## Required tests
- A answers, B interrupts, C adds: A's report contains only A utterances.
- Label swap after re-cluster: no evidence moves.
- Unknown speaker: evidence quarantined.
- Rename mid-meeting: same participant id.
- Human correction: re-attributed, audit logged.
- Mixed-audio-only adapter + multiple candidates: individual reports refuse to emit without recruiter confirmation.
