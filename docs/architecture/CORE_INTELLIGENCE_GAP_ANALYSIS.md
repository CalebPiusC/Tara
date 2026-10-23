# Core Intelligence Gap Analysis

**Review date:** 2026-10-23  
**Compared against:** `Tara_Core_Intelligence_Architecture.pdf` and the repository implementation available on this date.  
**Purpose:** A continuation point for work on Tara's context, memory, turn-taking, attribution, and meeting synthesis. This is a code-grounded gap analysis, not a claim that live Gemini or Google Meet behavior has been validated.

## Summary

The architecture's core direction matches much of the repository: Tara has a live voice engine, bounded working memory, scoped durable memory, automatic session recording, a deterministic participation gate, interruption handling, speaker-attribution abstractions, and minutes/interview generation. The main gap is the end-to-end contract between these parts. Facts, decisions, speaker identity, consent, and generated records do not yet share one auditable evidence model, and quality is not yet demonstrated by replayable meeting scenarios.

Do not rewrite working components or expand integrations as the next step. First make the existing intelligence path traceable and testable.

## Status Against The Architecture

| Area | Present in the repository | Remaining gap |
|---|---|---|
| Live loop | Live audio/transcript events, session IDs, epochs, bounded queues, response cancellation, stale-response rejection, and reconnect handling exist in `voice/` and `core/`. | There is no single structured meeting-context projection joining live turns, current topic, decisions, actions, open questions, and uncertainty. `ConversationState`, `WorkingMemory`, in-memory meeting notes, and durable memory each hold different slices. |
| Working and long-term context | `WorkingMemory` bounds recent turns and keeps a small older-turn digest. PostgreSQL memory supports summaries, transcript chunks, facts, retrieval filters, corrections, and section scope. | The handoff from current discussion to durable, queryable facts is mediated by separate stores and prompts. Entity resolution and ambiguous references such as “that fix” remain largely keyword-driven. |
| Evidence and claims | `MemoryItem` requires source, session provenance, status, and confidence. The recorder checks extracted fact quotes against transcript text. Proposals and corrections have dedicated handling. | Many source references are session-level strings or quote metadata rather than stable transcript segment IDs with timestamps and speaker-confidence evidence. Confidence, explicit confirmation, automatic repetition, and completion are still represented across status and metadata rather than one typed provenance contract. |
| Turn-taking | The deterministic participation gate handles direct address, mute/listen-only, takeover, policy, and interview identity. The engine can wait for the floor and cancel playback on interruption. | The full action set in the PDF (respond, wait, clarify, offer help, silent) is not represented as one evaluated intervention policy. Proactive behavior and when to clarify need end-to-end measurement, not just gate unit tests. |
| Speaker attribution | Participant registries, confidence-bearing attribution, `unknown`, and a versioned diarization-label map exist. | The Meet adapter documents a mixed stream: one-person interview attribution is a shortcut; multiple people become `mix`/unknown. General multi-speaker diarization and identity mapping are not verified in real calls. |
| Meeting synthesis | The recorder stores transcript chunks and generates summaries/candidate facts. Minutes separate confirmed items, proposals, open questions, and unconfirmed items. | The minutes model input mostly contains rendered facts and recent excerpts; it does not consistently retain clickable/source-segment evidence for every statement. Abrupt-session recovery exists, but the generated artifact should visibly say when the meeting was incomplete. |
| Privacy and policy | Disclosure text is required by meeting configuration; raw audio is not normally retained; transcript redaction exists for selected sensitive categories; tools are allow-listed and permission-gated. | A disclosure string is not proof of participant consent. The security/privacy design explicitly remains **Designed, not implemented** for consent, authenticated access, deletion controls, and complete retention enforcement. Redaction is heuristic and not comprehensive. |
| Evaluation | Offline tests cover gates, memory, ranking, corrections, recovery, document boundaries, and tool behavior. A small labeled memory regression set now exists. | There is no comprehensive replayable end-to-end scenario set or measured baseline for grounded answers, intervention quality, speaker attribution, synthesis omissions, and recovery. Full-suite real-Chrome audio has a known timing/threshold flake; live-model quality remains unverified. |
| Integrations | Browser-based Meet and document/email-related workflows exist in code. | The PDF recommends stabilizing core intelligence before broadening integrations. Existing integration code should not be treated as evidence that all live behavior is dependable. |

## Recommended Work Sequence

### P0: Define An Evidence-Linked Meeting Context Contract

Add a small typed meeting-context projection rather than replacing the existing stores. Every fact should identify:

- `meeting_id` and `session_id`;
- source transcript/event IDs and timestamps;
- speaker ID, attribution confidence, and identity source when available;
- item kind and claim state (`proposed`, `confirmed`, `superseded`, `unknown`);
- confirmation method and confirmer, if any;
- confidence, sensitivity classification, and supersession link.

The context projection should be updated by application-owned events. Models may suggest a fact or topic, but may not set confirmation, speaker identity, consent, or permissions. Preserve the current stores behind adapters until the projection is validated.

**Acceptance checks:** a proposed decision never appears as confirmed; an unknown speaker remains unknown; a correction supersedes only its intended predecessor; every projected fact resolves to its source segment; stale events cannot overwrite a newer meeting version.

### P1: Enforce Consent, Retention, and Access Before Capture/Storage

Turn the existing privacy design into application-enforced policy. Distinguish presence disclosure from consent; define what happens when someone objects; ensure recording, transcript persistence, model transmission, and artifact sharing each obey their own policy. Define retention and deletion behavior for transcript chunks, summaries, extracted facts, and generated documents. Make real-user access derive from authenticated identity rather than local defaults.

**Acceptance checks:** no persistent capture before required consent; denied consent prevents persistence and cloud transmission; deletion removes or marks every supported artifact; unauthorized-user canaries never appear in retrieval or generated context.

### P2: Carry Evidence Into Minutes

Keep the existing minutes categories, but pass structured evidence references with every decision, action, disagreement, and open question. Include speaker and timestamp when known, and explicitly preserve “proposed,” “confirmed,” “completed,” and “unknown.” Label interrupted/recovered sessions as incomplete. Do not let a free-form summary create facts absent from the structured evidence.

**Acceptance checks:** every generated decision/action links to evidence; invented owners/deadlines are rejected or labeled unknown; disagreement survives synthesis; interrupted-session minutes cannot read as a normal completed meeting.

### P3: Evaluate Intervention Behavior With Replayable Traces

Build deterministic meeting traces for direct questions, ambiguous references, overlapping speech, long pauses, declined assistance, changed decisions, stale tool results, and interruptions. Record expected context state and acceptable speech outcomes. Measure unwanted interventions separately from missed direct requests; silence alone is not a success metric.

**Acceptance checks:** each scenario has an expected state transition and allowed response set; duplicate/stale answers are rejected; interruption and “stay silent” cases are verified; results report both false interventions and missed opportunities.

### P4: Verify Speaker Attribution In Realistic Multi-Person Conditions

Keep platform speaking indicators, diarization labels, and verified participant identity separate. Preserve attribution provenance and `unknown`; provide an authorized correction path. Do not use a best guess as a verified speaker for commitments or sensitive information.

**Acceptance checks:** overlapping/ambiguous turns stay unknown; identity mappings carry confidence and source; corrections are auditable; attribution precision is measured on consented traces before names are used in consequential minutes.

### P5: Establish Retrieval And Synthesis Baselines Before Tuning

Expand the small offline regression set into a labeled corpus with multi-meeting references, corrections, proposals, unanswerable questions, conflicting evidence, speaker uncertainty, and tenant/section canaries. Measure precision/recall or MRR, false-confirmation rate, citation validity, and abstention. Keep ranking weights explicitly provisional until a baseline exists.

**Acceptance checks:** zero unauthorized canary leakage; zero proposal-to-confirmed promotion without the documented rule; stale/superseded evidence is not presented as current; evaluation command and results are recorded with date/config.

## Suggested Next Task

Start with **P0, the evidence-linked meeting context contract**. First inventory the fields already carried by `UtteranceRecord`, `TurnRecord`, `MemoryItem`, and `MemoryRecord`; then specify the minimal shared projection and a traceability table to existing tests. Add one vertical slice for a decision from transcript to durable memory to recall/minutes before extending to every fact kind.

Do not add embeddings, a new database, or more meeting-platform integrations as part of that first slice. The current retrieval evaluation plan remains at [RAG_AND_MEMORY_EVALUATION](../memory/RAG_AND_MEMORY_EVALUATION.md); current privacy status is in [SECURITY_AND_PRIVACY](SECURITY_AND_PRIVACY.md).

## Verification Limits

This review compared repository code and docs with the 12-page architecture PDF on 2026-10-23. Offline tests establish deterministic behavior only. Real Gemini response quality, full multi-speaker attribution, consent workflows, and end-to-end accuracy of minutes remain unverified unless separately run and measured.