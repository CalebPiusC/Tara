# Context Assembly for Gemini

**Status: Designed. Not implemented.** Today `conversation.py` builds a fixed prompt from the last 20 transcript lines plus up to 3 keyword-retrieved passages; that is the Phase 1 stand-in. Parent: [MEMORY_AND_RAG](MEMORY_AND_RAG.md).

## Principle
More context is not better. Send the smallest context that is **relevant, authoritative, recent and permitted**. The whole knowledge base and the whole transcript are never sent.

## Budgets (configurable; no model limit is hard-coded)
The fetched Gemini model page (2026-10-19) did **not** state context windows for the Flash models, so the limit is a required config value (`TARA_MODEL_CONTEXT_TOKENS`), set by the operator after reading the model's documentation. Budgets are fractions of `usable = model_context_tokens - reserved_output_tokens`.

| Section | Starting fraction (to tune with the eval set, not measured) | Rule when over budget |
|---|---|---|
| System and runtime rules | fixed, small | never trimmed |
| Meeting config summary (purpose, mode, constraints, `no_commitments`) | small | compress to essentials |
| Latest question + active question | reserved | never trimmed |
| Recent conversation window | largest dynamic share | oldest lines roll into the summary |
| Working memory (rolling summary + pending questions) | medium | summarizer shortens |
| Meeting memories (decisions, actions, open questions) | medium | rank by relevance, then recency |
| Retrieved knowledge | medium | drop lowest-ranked chunks first, keep citations for those kept |
| Output reservation | `reserved_output_tokens` | fixed |
Token counting: use the provider's counting method once verified (Q-C1, **Unverified**); until then a conservative character-based estimate with a safety margin. Spoken answers are short, so output reservation is small.

## Assembly algorithm
1. **Trigger and live state** (from the runtime, not retrieval): latest utterance, speaker label with confidence, active question, mode, floor state.
2. **Query construction:** rewrite the trigger into a standalone query using the last turns (resolve "it", "that"), extract entities and constraints (product, version, project). Query rewriting is a model call: skipped when the trigger is already self-contained, to protect latency. Output is untrusted and length-capped.
3. **Scope:** `AccessScope` from the authenticated meeting. Never from query text.
4. **Parallel fetch with timeouts:** knowledge retrieval and meeting-memory recall run concurrently; each has a timeout; failure yields an explicit `retrieval_unavailable` marker, not silence.
5. **Select:** apply thresholds; dedupe (same chunk id, near-identical text, same source section); prefer the current version of a document; prefer authoritative kinds over statements.
6. **Order:** rules, config, evidence (retrieved + memories, each with ids), working summary, recent window, question last.
7. **Render** into delimited sections. Untrusted content is wrapped and labeled (`<doc id= version= date=>`), after sanitizing delimiter look-alikes (see `render_transcript` for the existing pattern). Instructions found inside documents are data.
8. **Record** (metrics only): section token counts, chunk ids used, retrieval latency, truncation events. No text in logs.

## Recent conversation, summarization and fact survival
- Keep the last N turns verbatim (N is a budget outcome, not a constant).
- When lines age out, the summarizer produces a rolling summary **plus structured carry-over**: decisions, commitments, unresolved questions, corrections, named entities. Structured items are stored as memory records with provenance and are re-injected by relevance, so they survive summarization even if the prose summary drops them.
- Corrections ("actually it's Friday not Monday") are stored as `supersedes` links, and the old statement is marked superseded rather than deleted.
- The summarizer is a model: its output is `inference` trust. A summary never raises a candidate to confirmed.
- Speaker uncertainty is preserved in summaries ("an unidentified speaker said ...").

## Topic change
Detect via low similarity between the new trigger and the current topic summary plus explicit cues; on change, start a new topic segment: retrieved chunks from the old topic are dropped from the active set (not from the database), the working summary keeps one line for the old topic.

## Conflicting information in the prompt
Conflicts are shown, not resolved: `<conflict>` block listing each claim with source, version and date, plus an instruction to state the disagreement. The model is not asked to pick a winner unless one claim is a newer version of the same document.

## Citations
Every evidence item has a stable `ref` (chunk id or memory id). The model must cite refs; after generation the validator checks: cited refs exist in the assembled context, and (pitch/engineering modes) factual sentences without refs are rejected or replaced (existing behavior in `validate_response`). Spoken answers carry short verbal attribution ("per the pricing page, updated March") rather than ids; ids go in the report.

## Failure fallbacks
Retrieval timeout: proceed with live + meeting memory and an `evidence_unavailable` flag; pitch mode then admits the limit. Assembly over budget: trim in the order above; if the mandatory sections alone exceed the budget, refuse to call the model and stay silent (log). Gemini failure: unchanged (silence, no improvisation).

## Metrics
Per turn: tokens per section, chunks retrieved/kept/cited, retrieval and assembly latency, truncations, cache hits. Targets are set after measurement ([RAG_AND_MEMORY_EVALUATION](RAG_AND_MEMORY_EVALUATION.md)).

## Open questions
Q-C1 token counting method. Q-C2 whether query rewriting pays for its latency. Q-C3 summarizer model and cost. Q-C4 topic-change detection quality.
