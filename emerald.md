# Tara Orchestrator: Architecture and Implementation

## 1. Project Overview

Tara is a real-time, voice-first AI assistant being developed to participate naturally in conversations, interviews, meetings, technical discussions, and other interactive situations.

The core voice experience is already working. Tara can receive voice input and respond naturally, with responses starting in approximately a second during successful tests.

We are now developing two parallel areas of Tara Core:

**Workstream A: Core intelligence, owned by another developer**
- Context management.
- Short-term and long-term memory.
- RAG and retrieval.
- Token and context-budget management.
- Persistent conversational knowledge.

**Workstream B: Orchestration, owned by you**
- Intent recognition and task routing.
- Tool selection and execution.
- Web search and research.
- Weather, location, and time information.
- Background tasks and asynchronous results.
- Technical research and explanations.
- Meeting, interview, and conversational assistance.
- Tool permissions, reliability, and observability.

You are responsible for Workstream B.

You must work on your own feature branch and integrate with the existing Tara Core architecture rather than building a separate assistant.

**Google Meet integration remains deferred.** Build platform-independent orchestration capabilities that can eventually support Google Meet and other interaction channels.

## 2. Start by Inspecting the Existing Project

Before implementing anything:

1. Read `AGENTS.md` and all relevant project documentation.
2. Inspect the repository structure and current branch.
3. Understand the Gemini Live provider, voice pipeline, event processing, session lifecycle, participation controls, and existing tool interfaces.
4. Determine how user input and model responses currently flow through the application.
5. Identify any existing orchestrator, agent loop, tool registry, background task system, or provider-native function-calling implementation.
6. Inspect existing dependencies and configuration before introducing alternatives.
7. Identify the interface boundaries between orchestration and the context/memory workstream.

Create a feature branch for your work. Do not commit directly to `main` or another developer's feature branch.

Before coding, produce a concise implementation plan based on the actual codebase.

Do not assume that a feature exists simply because a class, interface, or placeholder has been created.

## 3. Core Objective: Build the Orchestration Engine

Implement an orchestration layer that allows Tara to understand what a user wants, determine whether tools are necessary, execute the appropriate operations, and communicate the results naturally.

The conceptual flow should be:

1. Receive the user's request.
2. Understand the intent and conversational context.
3. Determine whether the answer can be given directly or requires external information.
4. Select an appropriate tool or combination of tools.
5. Validate the required arguments and permissions.
6. Execute the operation.
7. Return results to the orchestrator.
8. Evaluate whether additional work is necessary.
9. Produce a concise, accurate response using the results.
10. Preserve relevant task status and outcomes for subsequent conversation.

This must not become an unnecessarily complicated multi-agent framework.

Prefer a small, deterministic orchestration engine with well-defined interfaces. Use the model for semantic decisions where useful, and ordinary application code for predictable operations.

Do not send every user request to a web search or another expensive model call.

Simple conversational requests should continue to receive fast responses.

## 4. Implement a Unified Tool Registry

Create or extend a tool registry that allows Tara to discover and invoke supported capabilities through a consistent interface.

Each tool should define:

- A stable identifier.
- A human-readable description.
- An input schema.
- A typed result.
- Required permissions.
- Execution timeout.
- Error-handling behaviour.
- Whether execution is synchronous or asynchronous.
- Whether the operation has external side effects.
- Relevant cost and latency metadata, where useful.

The orchestrator should select tools based on their descriptions, capabilities, and the current request.

Implement argument validation and reject unsupported operations before execution.

Do not expose arbitrary shell commands, unrestricted code execution, credentials, or unrestricted filesystem access to the model.

External tools must run through controlled adapters.

Tool failures should be represented explicitly so Tara can recover, choose an alternative, or explain the limitation rather than inventing a result.

## 5. Implement Essential Information Tools

Tara should have access to basic, current information that a capable conversational assistant needs.

### A. Time

Support requests such as:

- "What time is it in Lagos?"
- "What time is it in Tokyo?"
- "How long until my interview?"
- "What day is it in London?"
- "Convert 3 PM Lagos time to New York time."

Requirements:

- Use reliable system time or a suitable time service.
- Use IANA time zones, such as `Africa/Lagos` and `Asia/Tokyo`.
- Handle daylight-saving changes correctly.
- Distinguish UTC from local time.
- Resolve relative dates and times using the relevant location's time zone.
- Never invent a time or silently assume the wrong time zone.

Basic time and time-zone conversions should not require web search or an LLM call when they can be calculated deterministically.

### B. Location

Support requests such as:

- "What is the capital of Nigeria?"
- "Where is Lagos?"
- "What time zone is this city in?"
- "Find the weather in Lagos."

Distinguish between geographic knowledge, the user's actual current location, and a location explicitly mentioned in a request.

Do not pretend Tara knows the user's precise physical location unless that information has been provided or a permitted location capability supplies it.

When a location is ambiguous, ask for clarification or explain the assumption.

### C. Weather

Implement a weather adapter using a suitable weather data provider.

Evaluate free or free-tier services before choosing a paid service. Verify their current API limits, licensing, attribution requirements, and availability.

The implementation should support:

- Current weather.
- Temperature and apparent temperature, where available.
- Humidity.
- Wind speed.
- Precipitation.
- Short-term forecasts.
- Weather alerts where supported.

Resolve city names to appropriate coordinates when required.

Use the weather provider for current conditions rather than relying on the model's training knowledge.

If the provider is unavailable, explain that current conditions could not be retrieved. Never fabricate weather data.

### D. Web Search

Implement a dedicated web-search tool.

When evaluating providers, investigate:

- Google Gemini's available search or grounding capabilities.
- Free or free-tier search APIs.
- Open-source search infrastructure where practical.
- Provider quotas, rate limits, attribution, licensing, and latency.
- Whether the provider can return source URLs, titles, publication dates, and useful excerpts.

Do not assume a search provider is free simply because it has an API.

Select a provider based on verified suitability rather than introducing multiple redundant providers immediately.

Keep the search implementation behind an interface so we can change providers without rewriting the orchestrator.

The search tool should return structured results containing available source information, not just a block of unverified text.

Tara must be able to distinguish between:
- Information retrieved from a source.
- Information inferred from retrieved evidence.
- Information already known from the conversation.
- Information that remains uncertain.

### E. Optional Research Tools

Inspect the existing architecture and identify other high-value tools that would benefit Tara, such as:

- URL and webpage retrieval.
- Documentation lookup.
- Technical API documentation search.
- Calculator and unit conversion.
- Structured data extraction.
- Safe summarization of retrieved documents.

Prioritize these according to practical usefulness, implementation cost, and latency.

Do not add every conceivable tool in the first implementation.

## 6. Build a Reliable Research Workflow

One of the most important capabilities is allowing Tara to research questions it cannot confidently answer from available information.

Example:

The user asks:

"Can you explain how the latest version of a technical library handles connection pooling?"

Tara should recognize that the answer may depend on current documentation.

The workflow should be:

1. Identify that current external information is needed.
2. Search appropriate sources.
3. Retrieve relevant pages or documentation.
4. Inspect the evidence.
5. Compare sources when necessary.
6. Construct an answer supported by the retrieved material.
7. Include useful source references.
8. Communicate uncertainty or conflicting evidence.
9. Return the result naturally in the ongoing conversation.

Do not treat search snippets as definitive evidence when the underlying page can be retrieved and inspected.

Prefer official documentation, primary sources, and relevant technical references.

For recent claims, verify publication dates or release information where possible.

Never fabricate URLs, sources, quotations, search results, or claims that a tool executed successfully.

### Multiple-step research

Some questions will require several searches.

Support bounded multi-step research where the orchestrator can:

- Form a research question.
- Search for evidence.
- Identify missing information.
- Perform another search when justified.
- Synthesize the findings.
- Stop when sufficient evidence has been gathered or a resource limit is reached.

Use explicit limits for tool calls, execution time, and model usage.

Avoid endless search loops or repeatedly searching for information already retrieved.

## 7. Support Natural Voice Interaction During Research

Tara is a voice-first assistant, not simply a text chatbot with speech added afterward.

When a user asks a complex question, Tara should be able to acknowledge the request naturally, conduct the research, and explain the findings.

For example:

User:
"Can you research the latest changes in Go's garbage collector and explain their impact?"

Tara may respond:

"Let me check the latest Go documentation so I can give you an accurate answer."

It should then conduct the research and return with the findings.

However, acknowledgement is not mandatory for every request. Short questions that can be answered immediately should not trigger unnecessary announcements.

### Important requirements

- Do not block the live audio event loop while searching.
- Do not wait for a lengthy research operation before returning control to the voice system.
- Do not speak long progress updates.
- Do not claim to be researching when no research task has actually started.
- Do not announce a guaranteed completion time unless it can be supported.
- Do not let a delayed research result interrupt a newer conversation unexpectedly.

The orchestration engine must distinguish between responding to the current turn and completing a longer-running task.

## 8. Implement Background Tasks

Some operations are too long to complete within a normal conversational turn.

Examples include:

- Researching a complex technical topic.
- Comparing several products or approaches.
- Reviewing multiple documents.
- Preparing a detailed interview briefing.
- Producing a meeting report.

Support background execution through a suitable task abstraction.

Each task should have:

- A unique identifier.
- A task type.
- Its originating session or conversation.
- A status.
- Creation and update timestamps.
- A deadline or timeout.
- Progress information where useful.
- A result or structured error.
- Cancellation support where practical.

Suggested states include:

`queued`, `running`, `completed`, `failed`, and `cancelled`.

Do not introduce a distributed job system unless the existing architecture requires it. An in-process worker may be sufficient for the initial local implementation, provided its limitations are documented.

If the process restarts, the system must not falsely report unfinished in-memory tasks as completed.

Long-running tasks should not consume unbounded memory, tokens, or provider requests.

### Delivering results

When a task completes:

- Make the result available to the originating conversation.
- Ensure the result belongs to the correct session.
- Avoid duplicate delivery.
- Respect the user's current participation state.
- Do not start speaking automatically if Tara has been instructed to remain quiet.
- Allow Tara to summarize the findings when the user returns or asks for them.
- If proactive delivery is supported, make it configurable and cancellation-aware.

If Tara is interrupted during a research task, that should not automatically destroy useful work. However, the system must prevent stale responses from being played after cancellation or a newer interaction.

## 9. Prepare for Interviews, Meetings, and Technical Discussions

The orchestrator should provide capabilities that are useful across multiple interaction types.

### Interview assistance

Potential capabilities include:

- Researching a company before an interview.
- Looking up technical documentation.
- Explaining unfamiliar concepts.
- Performing calculations.
- Summarizing relevant information.
- Preparing follow-up questions.
- Organizing notes and action items.

### Meeting assistance

Potential capabilities include:

- Retrieving relevant project information.
- Looking up unfamiliar terms.
- Researching technical questions.
- Summarizing retrieved evidence.
- Preparing concise explanations.
- Supporting later meeting summaries through the appropriate context and memory interfaces.

### Technical collaboration

Potential capabilities include:

- Searching official documentation.
- Checking API behaviour.
- Comparing technical approaches.
- Explaining code concepts.
- Retrieving relevant reference material.
- Producing structured findings with sources.

Keep these capabilities platform-independent.

Do not implement Google Meet APIs, meeting capture, browser automation, or Google-specific microphone integration in this phase.

The purpose is to prepare the orchestration capabilities that future meeting integrations can use.

## 10. Integrate with the Context and Memory Workstream

Another developer is implementing Tara's context management, memory, RAG, and token-management systems.

Coordinate with that developer before defining shared interfaces.

Do not independently create a competing memory system, duplicate database schemas, or implement a second context manager.

The orchestrator should consume clearly defined interfaces for operations such as:

- Obtaining relevant conversation context.
- Retrieving relevant stored information.
- Requesting explicit memory operations.
- Accessing session metadata.
- Providing research results for subsequent conversational use.

The exact interfaces must be derived from the existing repository and coordinated branch changes.

If the interfaces are not yet available, define a small documented contract and use an adapter or temporary test double for development.

Keep the orchestration engine independent of the memory storage implementation.

For example, the orchestrator should not need to know whether a memory retrieval operation uses PostgreSQL, Redis, full-text search, or vector similarity.

Similarly, the memory implementation should not need to know which weather or web-search provider is being used.

### Shared branch discipline

- Work exclusively on your feature branch.
- Do not modify another developer's branch.
- Avoid changing shared interfaces without coordination.
- Document new interfaces and their assumptions.
- Keep commits focused and reviewable.
- Rebase or merge only according to the team's agreed workflow.
- Do not push directly to `main`.

## 11. Security and Tool Permissions

The orchestrator must not blindly execute instructions obtained from web pages or retrieved documents.

Treat external content as untrusted input.

Implement appropriate protections against:

- Prompt injection in webpages.
- Malicious instructions embedded in documents.
- Unauthorized access to user data.
- Secret exposure.
- Unbounded tool execution.
- Excessive provider costs.
- Repeated tool-call loops.
- Unsafe external side effects.

Use least-privilege permissions.

Read-only operations such as weather retrieval and web search can generally run without additional confirmation.

Operations that modify files, send messages, spend money, change account settings, or perform other consequential actions should require appropriate authorization and confirmation.

Never expose API keys to the model or external search results.

Use environment-based configuration or the repository's established secret-management mechanism.

Add request timeouts, rate limits, input validation, and structured error handling.

## 12. Latency and Cost Management

Low latency is a core requirement.

The existing live voice experience must remain responsive throughout this phase.

Measure:

- Orchestration decision time.
- Tool-selection overhead.
- Search-provider latency.
- Page-retrieval latency.
- Background-task startup time.
- Total research duration.
- Time until the user receives the first spoken acknowledgement, when applicable.
- Time until the final result is available.
- Model usage attributable to orchestration.

Use deterministic implementations for simple tasks.

Examples:

- Time-zone conversion should use a time-zone library.
- Basic arithmetic should use a calculator.
- Current weather should use a weather provider.
- Current web information should come from a search or retrieval tool.

Do not use an LLM to perform work that can be completed reliably with a small deterministic function.

Use caching where appropriate, with sensible expiration for changing information.

Do not cache current weather or time-sensitive facts indefinitely.

Keep background research and memory extraction outside the critical audio-processing path.

## 13. Testing Requirements

Implement unit, integration, and appropriate end-to-end tests.

At minimum, test the following.

### Test A: Time

Ask:

"What time is it in Lagos and Tokyo?"

Verify correct time-zone handling without unnecessary web search.

### Test B: Weather

Ask:

"What's the weather in Lagos?"

Verify that the orchestrator obtains actual provider data, identifies the relevant location, and does not invent unavailable information.

### Test C: Current technical information

Ask:

"Research the latest official documentation for a technical library and explain a specific feature."

Verify source retrieval, evidence-based synthesis, and useful references.

### Test D: No external information needed

Ask:

"What is the difference between a stack and a queue?"

Verify that Tara can answer directly without unnecessarily invoking web search.

### Test E: Multi-step research

Ask a question requiring several related sources.

Verify bounded tool execution, source tracking, and a coherent final answer.

### Test F: Background execution

Start a longer research task.

Verify that the voice interaction remains responsive and that the task result becomes available when completed.

### Test G: Interruption

Interrupt Tara while research is running.

Verify that obsolete responses do not play and participation controls remain functional.

### Test H: Cancellation and timeouts

Cancel a task or simulate a provider timeout.

Verify that the task state is accurate and resources are cleaned up.

### Test I: Tool failure

Simulate unavailable search and weather providers.

Verify graceful failure without fabricated results.

### Test J: Security

Test malicious instructions embedded in retrieved pages, unauthorized tool requests, invalid arguments, and attempts to access another user's data.

### Test K: Shared context integration

Verify that the orchestrator consumes the agreed context and memory interfaces without duplicating their responsibilities.

### Test L: Regression

Run existing Tara Core tests, including voice, interruption, participation-state, and audio-streaming tests.

Distinguish mocked results from tests that actually call external providers.

## 14. Implementation Stages

Work incrementally.

**Stage 1: Architecture audit**
- Inspect the repository.
- Document existing orchestration and provider capabilities.
- Define boundaries with the context and memory workstream.

**Stage 2: Orchestration foundation**
- Implement or refine the orchestrator.
- Establish the tool registry and execution contracts.
- Add argument validation, permissions, timeouts, and structured errors.

**Stage 3: Essential information tools**
- Implement time and time-zone handling.
- Add location resolution where necessary.
- Integrate a suitable weather provider.
- Implement a web-search adapter.

**Stage 4: Research workflow**
- Add page retrieval, source handling, and evidence-based synthesis.
- Support bounded multi-step research.

**Stage 5: Background execution**
- Implement task tracking, cancellation, result retrieval, and safe delivery.
- Preserve voice responsiveness during long-running operations.

**Stage 6: Context integration**
- Integrate with the other developer's agreed interfaces.
- Ensure research findings can be used naturally in subsequent conversation.

**Stage 7: Verification**
- Add tests, measure latency, document configuration, and verify actual external-provider behaviour.

Do not attempt a large rewrite in one pass. Keep the project runnable after each stage.

## 15. Definition of Done

This workstream is complete when:

- Tara can decide when external tools are needed.
- Time and time-zone questions are answered correctly.
- Current weather can be retrieved from a real provider.
- Web search can retrieve useful, attributable sources.
- Technical research can be performed and summarized accurately.
- Longer research tasks can run without blocking the voice pipeline.
- Completed results can be returned to the correct conversation.
- Interruptions and participation controls remain reliable.
- Tool failures are handled honestly.
- Tool permissions and execution limits are enforced.
- The implementation integrates with the shared context and memory interfaces.
- Relevant tests pass and actual provider tests are clearly identified.
- The feature is committed to the developer's own branch with clear documentation.

## Final Instructions

Inspect the repository before editing. Follow `AGENTS.md` and existing conventions.

Prefer free or free-tier services where practical, but verify their current availability, quotas, and terms before choosing them.

Do not add unnecessary dependencies, duplicate infrastructure, or speculative abstractions.

Do not claim a feature works based only on mocks.

Do not begin Google Meet integration during this phase.

At the end, provide:

1. A concise architecture assessment.
2. The orchestration design and tool interfaces.
3. The tools and external providers implemented.
4. Configuration and environment variables required.
5. Files changed and the reason for each major change.
6. Tests executed and their actual results.
7. Before-and-after latency measurements where available.
8. Known limitations and remaining work.
9. The feature branch name and suggested integration steps.

The objective is to give Tara reliable tools and research capabilities while preserving the fast, natural voice experience that already works.