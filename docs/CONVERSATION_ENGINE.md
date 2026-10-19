# Conversation Engine

**Status: Designed, not implemented.**

## Role
Given runtime-approved participation, build a bounded prompt from state and call `LanguageModelProvider`; return a validated `ResponseProposal`. It never speaks or runs tools.

## Prompt assembly (not one giant prompt)
1. System: role + hard rules rendered from the **validated** `MeetingConfig` (not raw organizer text).
2. State slice: current question, candidates answered, decisions so far, open questions.
3. Recent transcript window with speaker labels **and confidence markers** (`[unknown]` where applicable).
4. Retrieved knowledge passages with ids (pitch/engineering).
5. Output schema: `{say: str, intent, cites: [passage_id], needs_tool: ToolCall|null, confidence, asks_clarification: bool}`.
Transcript text is *data*: delimited and labeled; instructions inside it are never obeyed as policy.

## Validation before VALIDATING state
Schema; length ≤ `max_utterance_sec` speech estimate; forbidden content (`no_commitments`); every factual product claim needs `cites` resolving to approved passages, otherwise rewrite to "I don't have that information"; interview mode forbids revealing rubric answers when configured; not stale.

## Interview engine (sub-component)
Plan = topics × difficulty; after each attributed answer the LLM proposes `{completeness, gaps[], follow_up?}`; engine enforces `max_per_question`; evidence entries `{candidate_id, utterance_ids, quote, criterion, observation|interpretation}`. Observations are quotes; interpretations are labeled and carry uncertainty. Reports are decision support; no hire/no-hire output unless explicitly configured and still marked for human review.

## Pitch grounding
`KnowledgeProvider.search(q)` → passages. Facts must trace to passage ids. Unknown → say so, log `unanswered_question`. Commitments/discounts blocked by config and by output filter.

## Failure
LLM timeout/malformed → one bounded retry, then speak nothing (or a short configured fallback line if `speak_on_failure`), log; never improvise. See [FAILURE_MODES.md](FAILURE_MODES.md).
