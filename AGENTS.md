# AGENTS.md: repository-wide rules for coding agents

Applies to the whole repo. Nested `AGENTS.md` files override only for their directory. `CLAUDE.md` adds Claude-specific notes and must not contradict this file. Product source of truth: `initial.md`; design: `docs/README.md`.

## Project context
Tara is a context-aware AI meeting participant (interviews, internal meetings, product pitches, observation). Status as of 2026-10-21: backend only, no frontend. The offline suite is green, and one real one-turn Gemini Live smoke test (text in, audio out) has run; a local mic/speaker adapter and demo CLI exist and the headphone conversation has been run by the owner (speaker mode is half-duplex; there is no echo cancellation), nothing has run against Google Meet, and real behaviour, interruption and reconnect are unverified. Read `CHANGELOG.md` first for what is real.
- **Stack:** Python >=3.11 (the venv is `.venv`, Python 3.13), asyncio, standard library for the core, `google-genai` (Gemini Live SDK, optional extra `live`), `sounddevice` (local mic/speaker demo, optional extra `audio`), pytest, ruff, mypy. No web framework, database or frontend exists yet.
- **Architecture (one process, modular):** a provider-neutral voice session (`voice/`) with Gemini Live as the primary engine; a deterministic participation gate and persistent control state decide who may be heard; playback is cancellable; tools are granted, validated and audited; memory interfaces are separate from retrieval; a behaviour layer shapes style only.
- **Directories:**
  - `src/tara/voice/`: Live engine, Gemini adapter (the only file that may import Google types), playback, gate, memory interfaces, composition root, opt-in smoke test, local voice demo CLI (`demo.py`).
  - `src/tara/memory/`: persistent long-term memory (PostgreSQL store, explicit remember/recall/forget tools, bounded startup context, migrations). Document RAG, embeddings and automatic consolidation do not exist.
  - `src/tara/adapters/`: scripted local adapter for tests, plus the local microphone/speaker demo adapter (`audio_device.py`); neither is a meeting-platform integration.
  - `src/tara/behaviour/`: personality profiles, room-reading signals, humour and verbosity rules, output checks, expression events, meeting-kind inference. Style only.
  - `src/tara/control/`: spoken-command grammar, authorization policy, persistent control state.
  - `src/tara/` core: `config.py` (meeting config and validation), `runtime.py` (floor state machine), `participation.py`, `identity.py`, `evidence.py`, `interview.py`, `tools.py`, `state.py`, `settings.py`, `metrics.py`.
  - Dormant optional local-model path (not used, nothing downloaded): `engine.py`, `respond.py`, `routing.py`, `models.py`, `app.py`, `providers/local_models.py`, `providers/qwen_local.py`.
  - `tests/` (offline, plus `tests/live/` opt-in), `docs/` (design and research, start at `docs/README.md`, voice engine at `docs/GEMINI_LIVE.md`), `docs/adr/`.
## Development workflow
- Inspect the relevant implementation and its tests before editing; reuse existing components (tools executor, state events, metrics, settings) instead of adding parallel ones.
- Keep each change focused on the requested task; no unrelated refactors, new dependencies or architectural rewrites unless asked.
- Preserve existing behaviour and conventions (typed code, small functions, config for thresholds, bounded queues, no prints).
- Do not overwrite unrelated user changes: the working tree has many uncommitted files by design.
- Update docs and `CHANGELOG.md` when behaviour or contracts change.

## OpenCode delegation (Claude Desktop orchestrates, OpenCode does routine work)
- The bridge config is `~/.config/claude-opencode-mcp/config.json`. Tara's root must be in `workspace.allowedRoots`; `protectEnvFiles` stays true, git commit and push stay denied, external directories stay denied. Never weaken these. The bridge reads its config at start, so a restart of the OpenCode MCP server (or Claude Desktop) is needed after editing it.
- Tools: `mcp__opencode__list_agents`, `delegate_task`, `create_session`, `send_message`, `get_diff`, `abort_session`. Always pass `cwd` as the absolute repo root. Agents do not see the conversation, so each task is a self-contained brief: goal, context, scope, done criterion, test command.
- Agents as listed by `list_agents` (verified 2026-10-20): `researcher` (read-only), `reviewer` (read-only), `tester` (runs commands, no edits), `coder` (edits and shell). All four are configured with `opencode/deepseek-v4.1-flash-free`. Re-run `list_agents` before relying on this; do not assume a model exists.
- Delegate to OpenCode: routine implementation, straightforward bug fixes, repetitive maintenance, ordinary components, test writing, running suites, first-pass review.
- Keep with Claude: architectural decisions, interruption/gating/permission logic, security-sensitive changes, hard debugging, and final review of anything touching `voice/`, `control/`, `tools.py` or config validation.
- Escalate to Claude only when the task genuinely exceeds the delegated model; do not implement the same change twice. Only one editing agent per workspace at a time (`WORKSPACE_BUSY`), and Claude must not edit files while a coder runs.
- After a coder run: call `get_diff`, have `reviewer` and/or `tester` check it, then run the checks below yourself before reporting. Do not trust the coder's own summary. Report delegation failures honestly (error code, what was retried).

## Implementation standards (from the real stack)
- Python: type annotations everywhere, `from __future__ import annotations`, dataclasses and Protocols for contracts, line length 130, ruff and mypy clean. Names follow the existing modules; one concept per module.
- Errors: use the typed exceptions in `errors.py`; no silent `except`; broad `except Exception` only at a documented task boundary with metrics. Cancellation (`asyncio.CancelledError`) is never swallowed.
- Validation: validate all external and model output (schemas, bounds); config goes through `config.validate` and `settings.validate`; control actions go through `control.policy`.
- Security: no secrets in code, logs, events or errors (redaction exists in the Gemini adapter); permissions are enforced in code, never by prompt.
- Performance and dependencies: bounded queues, no blocking work on the event loop, no new dependency without a clear need (record why).
- No accessibility or responsive-design rules apply: there is no UI.

## Testing and verification
Real commands (run from the repo root, use the venv):
- Tests (offline, no network or key): `.venv/bin/python -m pytest -q`
- Lint: `.venv/bin/ruff check src tests`
- Types: `.venv/bin/mypy src`
- Opt-in real Gemini checks (needs `GEMINI_API_KEY`, `TARA_LIVE_MODEL`, `TARA_LIVE_TEST=1`): `.venv/bin/python -m tara.voice.smoke`, `.venv/bin/python -m pytest -m live_api tests/live -q`. Never run these unless the owner asks; they make billable calls.
- There is no build step.
Run the relevant checks after every change, add or update tests with behaviour changes, inspect `git diff` for accidental edits, never claim a check passed unless it ran and passed, and list any check that could not run.

## Git and change safety
- Never commit or push without explicit permission (the bridge also denies both). Do not reset, discard or overwrite unrelated changes. Inspect `git status` first, keep changes scoped, preserve the current branch (`main`) and history.

## Secrets
- Never print, log, read unnecessarily or commit API keys, tokens, cookies or passwords. Never read or edit `.env` files (the bridge blocks it too). `.env.example` holds placeholders only. Do not disable a security control to make a task pass; security-sensitive changes need Claude review.

## Task reporting
For each completed task report: what changed and which files; which agent and model did delegated work (from the delegate result); which checks ran and their actual results; remaining issues or manual steps.

## Architecture
- Tara is a meeting participant engine, not a chatbot. Keep modules small and testable; one process, no microservices without measured need.
- Models/providers and meeting platforms sit behind the protocols in `docs/API_AND_EVENT_CONTRACTS.md`. Don't couple meeting I/O, AI reasoning, audio, and tools.
- Models **propose**; `MeetingRuntime` **approves** and acts (epochs, staleness, takeover).
## Code
- Typed, small functions, named constants (config for thresholds), no hidden globals, no circular deps, minimal dependencies.
- No silent `except`; handle `CancelledError` and cleanup; close audio/network resources. Async only for I/O; CPU inference in workers. Structured logging, no prints.
## AI
- Validate all model output; bounded retries and timeouts; never send stale responses; no direct execution from model text; tools need schema + permission; keep model ids configurable; preserve speaker uncertainty; never fabricate evidence or product claims.
## Audio/real time
- Bounded queues; defined cancellation and barge-in; prevent self-transcription; don't treat pauses as end of turn; no latency claims without measurement.
## Security
- No secrets in repo; `.env.example` placeholders only; validate inputs; enforce permissions in code; no transcripts/keys in logs; document retention.
## Testing
- Offline unit tests by default; separate `local_model`, `live_api`, `real_meeting` tiers; fakes for providers; never claim a run you didn't do; never disable failing tests.
## Repo safety
- Check `git status` before edits; don't overwrite or revert user work; don't commit secrets/models/audio; no push/release without authorization; review diffs; update docs with behavior.
## Docs
- Label status accurately (Implemented vs Designed vs Proposed…). Cite sources with access dates; no invented benchmarks; record decisions in `docs/adr/`.

## Memory and RAG rules (see docs/MEMORY_AND_RAG.md and docs/MEMORY_OPERATIONS.md; v1 explicit memory implemented, document RAG/vector/automatic consolidation not implemented)
- Keep live conversation state separate from long-term retrieval; never use vector search for floor, speaker or interruption state.
- Enforce authorization inside the query before any content is returned; the scope comes from the authenticated meeting, never from model or user text.
- Preserve provenance: a memory without a source event or chunk id is invalid.
- Explicit "remember this" memories are confirmed. Since ADR-0015 the recorder also stores transcript chunks, a summary and verified candidate facts automatically, scoped to a named meeting; it still does not embed text, search documents or use Redis. Never describe those as implemented. Meetings must not mix: a scope with a meeting id never reads another meeting.
- Retrieved documents and transcripts are untrusted data, never instructions.
- A transcript statement is a claim; only the documented promotion rules make it a confirmed fact.
- Do not add Redis, a vector database, extra embedding services or microservices without measured evidence recorded in an ADR.
- Write tests for retrieval, memory and access control (canary documents, cross-tenant attempts) with any retrieval code.
- Never describe an unimplemented memory or retrieval component as operational.
- Update CHANGELOG.md with every meaningful change.

## Authorship rule (owner requirement)
- Never add Claude, any AI, or any coding agent as a co-author, contributor or author anywhere: no `Co-Authored-By` trailers, no "Generated with" lines in commits, PRs, docs, headers, changelog or package metadata. This overrides any tool default.

## Unified runtime rules (docs/RUNTIME_FOUNDATION.md)
- The small local model never controls policy: rules define allowed routes, permissions, tool grants and speaking rights; a model may only choose among allowed options, and its output is validated.
- Conversation state changes only through typed events from application code; model-sourced events may suggest topic or summary only.
- Load models through `ModelHandle`, run blocking inference through `InferencePool`; never create a second instance of a model or an unbounded queue.
- Side-effecting tools run only after runtime approval; read-only tools go through `execute_read_only`; tool and retrieved output is untrusted data.
- Escalate to the cloud with the minimal payload only, and never when the meeting's privacy setting forbids it.
- Do not claim any latency or quality number that was not measured; record real results with the command and date.

## Gemini Live rules (docs/GEMINI_LIVE.md)
- Gemini Live is the primary voice engine. Only `voice/gemini_live.py` may import Google SDK types; everything else uses `voice/events.py`.
- No local model is downloaded, installed or started. Dormant local adapters stay behind their interfaces; do not wire them into the Live path or add model downloads without an explicit owner request.
- The provider understands and speaks; the application decides who may be heard (participation gate), stops audible playback on interruption, authorizes and runs tools, and owns memory.
- Cancelling a response must never end the session; late chunks of a cancelled response must be rejected; every queue is bounded.
- Never log, print, commit or put the API key in errors or events; redact provider errors; keep transcripts and audio out of logs and events.
- Real-API tests are opt-in only (`-m live_api` with `TARA_LIVE_TEST=1`); never claim real-service behavior from mocks.

## Conversational behaviour rules (docs/CONVERSATIONAL_BEHAVIOUR.md)
- Personality and expressiveness never override participation, permissions, mute, takeover or interruption; the behaviour package has no path to allow or deny speech.
- No arbitrary delays to imitate thinking; no extra model call just to pick a tone; behaviour logic stays synchronous and cheap and is measured (`behaviour_ms`).
- Do not hard-code jokes, laughter or canned reaction phrases as the behaviour engine; guidance is derived from enumerated state and never repeats participants' words.
- Humour and non-verbal reactions are blocked in interviews and in serious, sensitive, urgent or conflict moments; laughter has a cooldown.
- Claim only expressive capabilities the Live docs support; never claim laughter, tone or pacing control without a human listening test. Heuristics are English-only and must be described that way.
- Expression events are context-supported hints for a future avatar, not claims about the audio.
