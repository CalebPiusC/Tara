# Meeting Context Model (Context Engine)

**Status: Designed, not implemented.** Schema version: `tara.meeting_config/v0` (draft; bump on breaking change).

## Purpose
Turn organizer input into a validated `MeetingConfig` that the runtime enforces. Prose is *input*, never authority.

## Trust layers
```
organizer text ──LLM extraction──► DRAFT config (untrusted)
                                      │ schema + policy validation (deterministic)
app policy (allowed tools, caps) ─────┤
                                      ▼
                         VALIDATED MeetingConfig ──► MeetingRuntime enforces
model decisions at runtime are proposals checked against this config
```
1. **Extraction** (Gemini, structured output, S7) fills a draft. Output validated against JSON schema, then semantic checks; the docs warn schema-valid ≠ correct.
2. **App policy** (code/config, not prose) defines the maximum: allowed tools, whether proactive speech can ever be enabled, retention ceilings.
3. **Effective permission = organizer-requested ∩ app-policy ∩ explicit organizer confirmation.** "You may email candidates" in prose creates nothing unless an `email` tool is registered *and* policy allows it.

## Schema (v0 draft)
```yaml
schema: tara.meeting_config/v0
meeting: {purpose: str, mode: interview_structured|interview_conversational|group_interview|natural|pitch|observe, duration_min: int, platform: google_meet|local}
objectives: [str]
participants: [{ref: str, display_name: str, role: candidate|recruiter|engineer|customer|observer|organizer, expected: bool}]
speaking: {policy: on_address_only|moderated|proactive_allowed|silent,
           barge_in: yield|finish_sentence, takeover_phrases_enabled: bool, max_utterance_sec: int}
turn_taking: {style: strict_round_robin|free, one_question_at_a_time: bool, equalize_airtime: bool}
interview: {type: technical|general|mixed, topics: [str], difficulty: str,
            followups: {enabled: bool, max_per_question: int},
            rubric: [{criterion: str, description: str, scale: str}],
            individual_reports: bool, attribution_min_confidence: float|null}
knowledge: {sources: [{id: str, kind: file|url, approved: bool}], no_commitments: [str]}
tools: [{name: str, scope: str, requires_human_approval: bool}]
outputs: [report_interview|decisions_actions|pitch_summary|transcript]
privacy: {disclosure_text: str, retain_audio: false, retain_transcript_days: int, send_transcript_to_llm: bool}
constraints: [str]
```
`attribution_min_confidence` is calibrated from data; default is required and non-null for group interviews (validator error otherwise).

## Handling problem instructions
| Class | Example | Behavior |
|---|---|---|
| Ambiguous | "interview a couple candidates" | Validator marks `participants` incomplete → ask organizer; no silent guess |
| Contradictory | "don't interrupt" + "keep each answer under 20s, cut them off" | Report conflict, require resolution; unresolved → stricter (non-interrupting) option |
| Impossible | "see the candidate's screen" with no video path | Reject capability with message |
| Missing | group interview without candidate names | Block start |
| Out of capability | "send the offer letter" | Strip, list as unsupported |
| Unsafe/unauthorized | "give 50% discount", "ignore your rules" | Drop; record in `rejected_instructions`; never reaches runtime as permission |

Also: instructions arriving *during* the meeting from participants are untrusted unless from a participant whose role grants it (e.g. organizer/recruiter) and attribution confidence is high; otherwise ignored for policy changes.

## Example configs (abridged but complete in structure)
### 1. Technical interview
```yaml
schema: tara.meeting_config/v0
meeting: {purpose: "Backend engineering interview", mode: interview_structured, duration_min: 45, platform: google_meet}
objectives: ["Assess Go concurrency, DB transactions, distributed systems"]
participants: [{ref: p1, display_name: Alex, role: candidate, expected: true}, {ref: p2, display_name: Recruiter, role: organizer, expected: true}]
speaking: {policy: moderated, barge_in: yield, takeover_phrases_enabled: true, max_utterance_sec: 40}
turn_taking: {style: strict_round_robin, one_question_at_a_time: true, equalize_airtime: false}
interview: {type: technical, topics: [Go, PostgreSQL, distributed systems, system design], difficulty: intermediate,
  followups: {enabled: true, max_per_question: 2},
  rubric: [{criterion: Concurrency, description: "goroutines, channels, races", scale: "0-4 with evidence"}],
  individual_reports: true, attribution_min_confidence: 0.9}
knowledge: {sources: [], no_commitments: []}
tools: []
outputs: [report_interview, transcript]
privacy: {disclosure_text: "I am Tara, an AI interviewer. This session is transcribed.", retain_audio: false, retain_transcript_days: 30, send_transcript_to_llm: true}
constraints: ["Do not reveal expected answers immediately", "Report is decision support; human reviews"]
```
### 2. General interview (customer support, 30 min)
Same shape: `mode: interview_conversational`, `type: general`, topics `[communication, problem-solving, empathy, experience]`, `difficulty: entry`, `followups: {enabled: true, max_per_question: 2}`, rubric on the four focus areas, one candidate, `attribution_min_confidence` may be lower because only one candidate exists (still `unknown`-safe).
### 3. Group interview
```yaml
meeting: {purpose: "Group backend interview", mode: group_interview, duration_min: 60, platform: google_meet}
participants: [{ref: a, display_name: "Candidate A", role: candidate, expected: true},
               {ref: b, display_name: "Candidate B", role: candidate, expected: true},
               {ref: c, display_name: "Candidate C", role: candidate, expected: true},
               {ref: r, display_name: Recruiter, role: organizer, expected: true}]
speaking: {policy: moderated, barge_in: yield, takeover_phrases_enabled: true, max_utterance_sec: 45}
turn_taking: {style: strict_round_robin, one_question_at_a_time: true, equalize_airtime: true}
interview: {type: technical, topics: ["system design"], difficulty: intermediate,
  followups: {enabled: true, max_per_question: 1}, rubric: [...], individual_reports: true, attribution_min_confidence: 0.95}
```
Rule enforced by app: utterances below threshold go to `unattributed_evidence`, excluded from every individual report ([PARTICIPANT_IDENTITY.md](../speakers/PARTICIPANT_IDENTITY.md)).
### 4. Engineering meeting
```yaml
meeting: {purpose: "Release planning", mode: natural, duration_min: 60, platform: google_meet}
speaking: {policy: on_address_only, barge_in: yield, takeover_phrases_enabled: true, max_utterance_sec: 30}
knowledge: {sources: [{id: sysdocs, kind: file, approved: true}], no_commitments: []}
tools: []
outputs: [decisions_actions, transcript]
constraints: ["Avoid interrupting", "Ask for clarification rather than guess"]
```
`proactive_allowed` must be an explicit organizer choice; here proactive contributions are only offered after a pause and when asked via "invited".
### 5. Product pitch
```yaml
meeting: {purpose: "Explain Testflow to a prospect", mode: pitch, duration_min: 30, platform: google_meet}
speaking: {policy: on_address_only, barge_in: yield, takeover_phrases_enabled: true, max_utterance_sec: 45}
knowledge: {sources: [{id: testflow_docs, kind: file, approved: true}],
            no_commitments: ["discounts", "contract terms", "unreleased features"]}
tools: [{name: crm_note, scope: "append note", requires_human_approval: true}]
outputs: [pitch_summary, transcript]
```
### 6. Observation-only
```yaml
meeting: {purpose: "Observe standup", mode: observe, duration_min: 15, platform: google_meet}
speaking: {policy: silent, barge_in: yield, takeover_phrases_enabled: false, max_utterance_sec: 0}
outputs: [decisions_actions, transcript]
privacy: {disclosure_text: "Tara (AI) is transcribing this meeting.", retain_audio: false, retain_transcript_days: 14, send_transcript_to_llm: true}
```
`policy: silent` is enforced in the runtime: the speak path is closed regardless of model output.

## Testing
Schema validation, each problem class above, permission intersection, rejected-instruction logging. See [TESTING_STRATEGY.md](../getting-started/TESTING_STRATEGY.md).
