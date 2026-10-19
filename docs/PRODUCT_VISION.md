# Product Vision

**Status: Designed, not implemented.** Source of truth: [`../initial.md`](../initial.md).

Tara is a context-aware AI meeting participant. It joins a meeting, understands why it exists, tracks who says what, and participates only as the organizer's instructions, the meeting state, and application-enforced permissions allow. One engine, many modes: interviews (technical, general, group), internal meetings, product pitches, observation-only.

## Core principle
Tara understands the meeting before deciding how to take part. Behavior = organizer instructions + objective + participants + conversation + meeting state + permissions + speaking rules + available knowledge/tools. **The LLM is one component.** Deterministic code owns meeting state, speaking permission, interruption, tool execution, and identity.

## Why each part exists (what breaks without it)
| Part | Without it |
|---|---|
| Context engine | Organizer text is just a prompt; permissions leak into prose |
| Participation engine | Model talks whenever it has something to say; interrupts humans |
| Speaker attribution with uncertainty | Candidate A's answer is scored as B's |
| Staleness checks | A 5-second-old answer plays after a human took over |
| Knowledge grounding | Pitch mode invents features and discounts |
| Human takeover | Tara keeps talking over the recruiter |

## Scope now
Backend, integrations, audio pipeline, participant intelligence, context engine, execution rules. **No frontend.** Hybrid local/cloud: STT/TTS/VAD local; Gemini is cloud. **Not offline.** Avatar is a future extension.

## Non-goals
Autonomous hiring decisions (reports are decision *support*; human review is default), unattended tool use beyond granted permissions, recording without disclosure.

## Worked scenarios
The eight scenarios (A–H) in `initial.md` §2 are the acceptance narratives; they map to simulation tests in [TESTING_STRATEGY.md](TESTING_STRATEGY.md) and to example configs in [MEETING_CONTEXT_MODEL.md](MEETING_CONTEXT_MODEL.md).
