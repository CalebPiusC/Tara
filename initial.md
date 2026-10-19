# TARA: MASTER PRODUCT DEFINITION, ARCHITECTURE, RESEARCH & ENGINEERING FOUNDATION

You are acting as the principal software architect, senior backend engineer, AI systems researcher, real-time audio engineer, technical writer, and hackathon technical lead for a project called **Tara**.

Your responsibility is to understand the complete product vision, inspect the existing repository, research the difficult technical components, establish a technically credible architecture, and create an exceptional documentation foundation that will guide the entire implementation.

This is not a request to build a generic AI chatbot.

We are building an AI team member that can join meetings, understand context, distinguish participants, decide when to speak, hold conversations, conduct interviews, represent products, and execute authorized tasks through supported integrations.

Your work must be technically grounded, specific to this product, well researched, implementation-oriented, and suitable for a serious engineering hackathon.

Do not produce shallow documentation filled with generic statements about AI.

Do not invent completed features, tested integrations, performance benchmarks, model capabilities, or API support.

Do not start implementing the entire product before establishing its architecture and documenting the decisions that will govern implementation.

First understand. Then inspect. Then research. Then document. Then validate the documentation against the actual repository.

---

# PART 1: UNDERSTAND THE PRODUCT

## 1.1 What is Tara?

Tara is a context-aware AI meeting participant and digital team member.

Tara should be capable of joining supported online meetings, understanding why the meeting exists, following the conversation, recognizing who is speaking, and participating according to the instructions and permissions established before the meeting.

Tara should behave like a competent human team member.

A competent team member does not speak continuously simply because they have something to say. They listen, understand the discussion, recognize when someone addresses them, decide whether their contribution is useful, wait for appropriate moments, respond to questions, and respect the person leading the meeting.

Tara must follow the same general principles.

The organizer provides meeting context before the meeting begins. Tara uses that context to establish its role, objectives, responsibilities, speaking behavior, participant information, restrictions, and expected outcomes.

The same underlying system must support multiple types of meetings without requiring a separate AI implementation for each use case.

Examples include:

- Technical interviews.
- General employment interviews.
- Mixed technical and behavioral interviews.
- Group interviews.
- Internal company meetings.
- Engineering discussions.
- Product planning meetings.
- Product demonstrations.
- Sales presentations and product pitches.
- Customer discovery.
- Technical support meetings.
- Meetings where Tara primarily observes and produces reports.

These are different operating modes of one product, not separate products.

## 1.2 The core product principle

**Tara must understand the meeting before deciding how to participate in it.**

Its behavior must be determined by the combination of:

1. The organizer's instructions.
2. The meeting objective.
3. The participants and their roles.
4. The current conversation.
5. The current meeting state.
6. The permissions granted to Tara.
7. The rules governing when Tara can speak or act.
8. The available information and tools.

The language model is only one component of the system.

The application itself must control meeting state, speaking permissions, interruption handling, tool execution, identity associations, and other deterministic responsibilities.

Do not delegate all responsibility to a single enormous prompt sent to Gemini.

---

# PART 2: CONCRETE EXAMPLES OF THE PRODUCT

Every major architectural decision in the documentation should connect to realistic use cases.

Do not document components in isolation. Explain why each component exists and what would happen without it.

## 2.1 Example A: Technical interview

An organizer configures:

- Meeting purpose: Backend engineering interview.
- Candidate: Alex.
- Position: Go Backend Engineer.
- Duration: 45 minutes.
- Topics: Go, PostgreSQL, distributed systems, and system design.
- Difficulty: Intermediate.
- Interview style: Structured.
- Follow-up questions: Enabled.
- Individual evaluation: Enabled.
- Human takeover: Enabled.

The organizer provides additional instructions:

"Evaluate the candidate's understanding of Go concurrency, database transactions, and distributed systems. Ask follow-up questions when the candidate gives an incomplete answer. Do not reveal the expected answer immediately. Give the candidate enough time to explain their reasoning. Keep the interview conversational and respectful."

Tara joins the meeting.

Tara introduces itself, explains the interview format, and asks:

"How would you prevent duplicate payments if a client retries a request after a timeout?"

Alex answers:

"I would use an idempotency key."

Tara should recognize that the answer is relevant but incomplete.

It might follow up:

"How would you store and validate that key so that two concurrent requests cannot create duplicate payments?"

Alex responds with an explanation involving a database uniqueness constraint and a transaction.

Tara evaluates the answer against the configured rubric, tracks the evidence supporting the assessment, and continues with an appropriate question.

At the end, Tara produces a structured report containing:

- Questions asked.
- Candidate responses.
- Follow-up questions.
- Evidence supporting each assessment.
- Strengths.
- Areas requiring further investigation.
- A summary against the configured evaluation criteria.

Tara must not invent evidence or silently confuse one candidate's answer with another candidate's answer.

It should distinguish observed evidence from uncertain interpretation.

The documentation must explain the difference between generating an evaluation and making a final hiring decision. Human review should remain available and should be the default for consequential employment decisions.

## 2.2 Example B: General interview

An organizer configures a customer support position interview.

The context specifies:

- Interview type: General.
- Focus: Communication, problem-solving, customer empathy, and experience.
- Duration: 30 minutes.
- Difficulty: Entry-level.
- Style: Conversational.

Tara asks:

"Tell me about a time you had to handle a frustrated customer."

The candidate responds with a story.

Tara recognizes that the candidate has explained the situation but has not clearly described the outcome.

It follows up:

"What did you do to resolve the situation, and what happened afterward?"

The follow-up should depend on the actual answer and the interview objective.

Tara should not simply move through a fixed list of questions regardless of what the candidate says.

## 2.3 Example C: Group interview with multiple speakers

A meeting contains:

- Tara.
- Candidate A.
- Candidate B.
- Candidate C.
- A human recruiter.

The recruiter instructs Tara:

"Conduct a group interview. Ask one question at a time. Give each candidate an opportunity to respond. Address candidates by name when possible. Prevent one person from dominating the discussion. Ask follow-up questions when appropriate. Do not attribute an answer to someone unless the speaker identification is sufficiently reliable."

Tara asks:

"How would you design a system that handles one million requests per day?"

Candidate A answers.

Candidate B interrupts with a different approach.

Candidate C adds a suggestion.

Tara must track:

- Who is speaking.
- What each person said.
- Which question they are responding to.
- Whether they are addressing Tara or another participant.
- Whether they are interrupting.
- Whether the current interview rules permit them to speak.
- Which candidates have already answered.
- Which candidates still need an opportunity to answer.

Tara should not interpret Candidate B's interruption as Candidate A's answer.

It should not give Candidate A credit for an idea introduced by Candidate C.

It should not generate an individual candidate assessment using mixed or incorrectly attributed statements.

When the identity of a speaker is uncertain, Tara must preserve that uncertainty rather than invent an identity.

## 2.4 Example D: Ordinary company meeting

An organizer provides this context:

"Join our engineering planning meeting. The team is discussing the next release of our application. Listen to the discussion, answer questions directed at you, and contribute when you have a useful technical observation. Avoid interrupting. If the team makes a decision, record it with the supporting context. If you are unsure about something, ask for clarification rather than guessing."

During the meeting, an engineer asks:

"Tara, what happens if our Redis cache becomes unavailable?"

Tara recognizes that it has been addressed and responds using the actual system documentation and the available discussion context.

Later, two engineers debate a database migration.

Tara does not interrupt merely because it has a possible answer.

If the team reaches a decision, Tara records the decision, its rationale, and any unresolved questions.

The documentation must explain how the system distinguishes:

- Listening.
- Being directly addressed.
- Receiving an instruction.
- Being invited to contribute.
- Having a useful unsolicited contribution.
- Being told to remain silent.
- Being interrupted.
- Yielding control to a human.

## 2.5 Example E: Pitching our own product

This is a major product capability, not a joke or a minor edge case.

Imagine that we are developing a product called Testflow.

An organizer instructs Tara:

"Join a meeting with a prospective customer. Your objective is to explain Testflow for Everybody, understand the customer's needs, demonstrate relevant capabilities, and answer questions using our approved product documentation. Do not claim that unavailable features exist. If you do not know an answer, acknowledge the limitation. Do not offer unauthorized discounts or make contractual commitments."

Tara joins the meeting.

The customer asks:

"What does your product do?"

Tara explains the product using its approved knowledge base.

The customer asks whether it supports a particular feature.

Tara checks the available product information and answers accurately.

The customer raises an objection.

Tara responds in context without becoming aggressive or repeating a memorized sales script.

If the customer requests a feature that does not exist, Tara acknowledges that limitation rather than pretending the feature is available.

After the meeting, Tara produces:

- Customer requirements.
- Questions asked.
- Objections raised.
- Product capabilities discussed.
- Unanswered questions.
- Potential next steps.
- Actions requiring human approval.

The architecture must support grounding responses in approved documents and separating verified product facts from generated language.

Future capabilities may include product demonstrations, customer discovery, lead qualification, and support conversations.

## 2.6 Example F: Tara is addressed by name

A participant says:

"Tara, can you explain the deployment process?"

The system should detect that Tara has been addressed, interpret the request, determine whether the request is within its responsibilities, and respond if permitted.

The system should not rely exclusively on matching the word "Tara."

The documentation should consider:

- Direct address by name.
- Natural variations in phrasing.
- Questions that clearly refer to Tara without using its name.
- Speech recognition errors.
- Other people with similar names.
- Ambiguous requests.
- False positives.

A wake-word or name detector may be useful, but it must be evaluated as part of the complete conversation system.

## 2.7 Example G: Human takeover

Tara is explaining a product.

A human team member says:

"Thanks, Tara. I'll take it from here."

Tara should yield control.

If Tara is already speaking, the system must stop or interrupt audio playback according to the meeting's control policy.

The architecture should distinguish between:

- Generating a response.
- Approving a response for delivery.
- Beginning speech playback.
- Actually delivering speech.
- Interrupting playback.
- Acknowledging human takeover.

A response that was appropriate five seconds ago may become inappropriate after the meeting state changes.

Do not send stale generated responses into a meeting.

## 2.8 Example H: Natural group conversation

The organizer chooses natural conversation mode.

Participants may speak freely. Tara should understand the discussion and contribute when appropriate.

Unlike a structured interview, Tara does not always wait for a fixed turn assignment.

However, it must still respect:

- Human speaking turns.
- Direct questions.
- Interruptions.
- Meeting objectives.
- Silence instructions.
- Human takeover.
- Uncertain speaker attribution.
- Its own permissions.

This mode is more difficult than structured interviews and should be documented as a separate implementation and testing challenge.

---

# PART 3: CURRENT DEVELOPMENT SCOPE

This section is extremely important.

## 3.1 We are building integrations first

We are not building the user-facing frontend in the current phase.

Do not start implementing a dashboard, landing page, authentication interface, or other user interface unless the existing repository already contains something that must be understood or preserved.

The immediate objective is to establish the backend, AI integration, meeting integration, audio pipeline, participant intelligence, context engine, and execution rules.

We need a working technical foundation, not an attractive interface that hides an incomplete backend.

## 3.2 Local development first

Initial development and testing will happen locally on an Apple Silicon MacBook with an M4 Pro chip and 24 GB of unified memory.

The local machine is the primary development environment.

We intend to run local speech-processing components and evaluate local models.

Model downloads will happen when the corresponding integration is ready to be implemented and tested.

**Do not download large models during the documentation and research phase.**

Do not consume unnecessary disk space, memory, or bandwidth before there is a clear reason.

Do not assume that every model will run comfortably in real time simply because it supports Apple Silicon.

Research model requirements and create a local setup guide that explains:

- What needs to be installed.
- Why it is needed.
- Expected storage requirements where verifiable.
- How to download models when their implementation phase begins.
- How to verify that a model works.
- How to benchmark latency and resource usage.
- How to disable or replace the model.
- What alternatives exist if the model performs poorly.

We will download the selected models when we reach their implementation phases.

## 3.3 Intended AI and audio components

The initial intended stack is:

**Speech recognition:** Whisper or a suitable implementation such as faster-whisper, subject to research and benchmarking.

**Reasoning and language generation:** Gemini Flash API, using a currently supported model identifier verified against official documentation.

**Text-to-speech:** Kokoro, evaluated for local inference and suitability for conversational speech.

**Speaker diarization:** A separate component if the selected audio integration does not already provide reliable speaker attribution.

**Voice activity detection:** A suitable VAD implementation to detect speech activity and support turn-taking.

**Meeting integration:** A verified approach for joining and exchanging supported media with Google Meet.

**Backend:** Python and FastAPI are reasonable initial candidates, but inspect the repository before making a final decision.

**Real-time communication:** WebSockets or another justified transport for internal events and streaming.

**Future avatar:** A separately designed video-generation and synchronization component.

These are intended directions, not permission to invent technical capabilities or ignore alternatives.

Research and document the trade-offs before finalizing each major choice.

## 3.4 Hybrid local/cloud architecture

Tara is initially a hybrid local/cloud system.

Speech recognition and speech generation are intended to run locally where practical.

Gemini provides cloud-based reasoning.

The documentation must accurately explain the consequences:

- Internet connectivity is required for Gemini API calls.
- Audio and transcript data sent to external providers may have privacy implications.
- API keys must be protected.
- Model latency affects response time.
- Local inference consumes system resources.
- Model selection must account for accuracy, latency, memory, licensing, and installation complexity.

Do not describe Tara as fully offline.

## 3.5 Future avatar

A realistic animated avatar is a future capability.

The eventual avatar should be able to:

- Represent Tara visually.
- Generate or animate a face.
- Synchronize mouth movement with generated speech.
- Integrate with the meeting media pipeline.
- Start, stop, and interrupt alongside speech playback.
- Handle changes in speech timing.
- Avoid drifting out of synchronization.

Research possible architectures and candidate technologies, but do not claim that an avatar has been implemented or tested if it has not.

Do not make the avatar a hard dependency for the first working voice-based system.

The core system should work independently of the avatar.

Document the interface through which a future avatar service would receive audio, timing information, and relevant state changes.

---

# PART 4: REPOSITORY INSPECTION BEFORE WRITING

Before changing anything, inspect the repository thoroughly.

Determine:

- The repository's actual root.
- Existing source files.
- Existing documentation.
- Existing package and dependency files.
- Existing application architecture.
- Existing configuration.
- Existing tests.
- Existing integrations.
- Existing naming conventions.
- Existing Git status and uncommitted changes.
- Existing Claude Code instructions.
- Existing agent instructions.
- Existing architectural decisions.

Do not assume the repository is empty.

Do not overwrite existing work without understanding it.

Do not delete files just because you would have organized the project differently.

Identify existing files named or functioning as:

- README.md
- CLAUDE.md
- AGENTS.md
- CONTRIBUTING.md
- LICENSE
- .gitignore
- pyproject.toml
- requirements.txt
- package.json
- Docker configuration
- Environment examples
- Architecture documents
- Test configuration

Inspect the actual files, not just their filenames.

If the repository already contains code, determine whether the current documentation describes the existing implementation or the intended future architecture.

Keep those two things distinct.

If major information is missing, document the uncertainty and research it.

Do not stop simply because the repository differs from your expectations.

---

# PART 5: RESEARCH BEFORE ARCHITECTURAL COMMITMENTS

Conduct focused, current technical research after inspecting the repository.

This is real research, not merely writing down remembered information.

Use available search tools and official documentation wherever possible.

Prefer primary sources in this order:

1. Official API documentation.
2. Official model repositories and model cards.
3. Official framework documentation.
4. Original research papers.
5. Maintainer documentation and repositories.
6. Credible engineering reports and benchmarks.
7. Community discussions when they provide relevant implementation experience.

Use community reports to understand practical problems, not as the sole source for important API capabilities.

Record when the research was performed.

For important claims, provide direct source links and explain what the source establishes.

Do not invent URLs, benchmarks, model capabilities, licenses, or API availability.

If you cannot verify a claim, label it as unverified and explain how it could be tested.

## 5.1 Research area: Google Meet integration

This is a high-priority research task.

Determine the currently supported ways an external application can:

- Join a Google Meet as a participant.
- Receive meeting audio.
- Access participant identity information.
- Access participant-level audio, if supported.
- Access meeting events.
- Send generated audio into a meeting.
- Handle disconnections and reconnect.
- Integrate video or a virtual camera if required in the future.

Investigate the distinction between:

- Google Meet REST APIs.
- Meeting metadata and recordings.
- Real-time media APIs or SDKs.
- Browser-based meeting participation.
- Virtual audio devices.
- Virtual camera integration.
- Browser automation.
- Any available developer previews or access restrictions.

Do not assume that an API for meeting metadata also provides live audio access.

Do not assume that an application can send arbitrary audio into Google Meet just because it can join a meeting.

Do not assume a meeting URL is sufficient to authenticate or participate.

Verify current API availability and access requirements from official sources.

Determine which approaches are suitable for a local development environment.

Compare realistic options in a research document.

For each option, explain:

- How it works.
- Required permissions.
- Whether it is officially supported.
- Whether access is restricted.
- What media can be received.
- What media can be transmitted.
- Whether participant identity can be associated with audio.
- Main limitations.
- Implementation complexity.
- Suitability for a hackathon.
- The recommended proof of concept.

Do not choose a method solely because it is the easiest to describe.

## 5.2 Research area: Speech recognition

Compare the intended Whisper implementation with reasonable alternatives.

Investigate:

- Accuracy.
- Streaming support.
- Incremental transcription.
- Latency.
- Apple Silicon compatibility.
- Memory consumption.
- Model sizes.
- Language support.
- Timestamp quality.
- Integration complexity.
- Licensing.
- Suitability for live conversation.

Distinguish between an implementation that transcribes completed audio segments and one that supports low-latency streaming behavior.

Do not claim that standard Whisper automatically provides robust real-time speaker identity.

## 5.3 Research area: Speaker diarization and attribution

Research how to identify speakers in live meetings.

Compare:

- Participant-level audio supplied by a meeting integration.
- Diarization on mixed audio.
- Voice embeddings or speaker representations.
- Known participant mappings.
- Self-introduction and identity confirmation.
- Combining meeting metadata with audio evidence.

Explain the difference between identifying a speaker change and determining a real person's identity.

Investigate overlapping speech, short utterances, noisy audio, similar voices, and speaker-label instability.

Determine what can be done locally on the target hardware.

Recommend a strategy for the initial proof of concept and explain its limitations.

Do not claim perfect identification.

## 5.4 Research area: Voice activity detection and turn-taking

Research suitable local VAD options and how they interact with transcription.

Document:

- Speech onset detection.
- Speech endpoint detection.
- Silence handling.
- Background noise.
- Short pauses.
- Long pauses.
- Overlapping speech.
- End-of-turn estimation.
- Barge-in.
- False activation.
- Latency.

Explain why a pause does not necessarily mean a participant has finished speaking.

Design an approach that combines audio signals, conversation context, and meeting state.

## 5.5 Research area: Gemini integration

Verify the current Gemini API documentation.

Research:

- Current supported model identifiers.
- Authentication.
- Rate limits and quotas where documented.
- Structured output capabilities.
- Streaming options.
- Tool/function calling.
- Error handling.
- Retry behavior.
- Context limits.
- Safety considerations.
- Data handling and privacy implications.

Do not hardcode an unverified model name into architectural documentation.

Recommend a configurable model identifier and a clear way to switch models.

The application should not depend on one model's undocumented behavior.

## 5.6 Research area: Kokoro and local text-to-speech

Investigate:

- Official repository and supported installation method.
- Available model artifacts.
- Voice selection.
- Streaming or chunked generation.
- Audio output formats.
- Apple Silicon compatibility.
- Memory requirements.
- Licensing.
- Inference latency.
- Audio quality.
- Interruption behavior.

Explain whether the selected implementation can generate speech incrementally or whether it requires complete text input.

Document practical implications for conversational response latency.

## 5.7 Research area: Real-time application architecture

Research the appropriate architecture for a long-running, event-driven voice system.

Investigate:

- FastAPI and asynchronous execution.
- WebSockets.
- Task cancellation.
- Audio buffering.
- Backpressure.
- Concurrent requests.
- State management.
- Timeouts.
- Retries.
- Idempotency.
- Graceful shutdown.
- Process supervision.
- Resource limits.
- Observability.
- Failure recovery.

Explain where asynchronous processing is beneficial and where it introduces unnecessary complexity.

Do not use a distributed architecture merely to make the diagram look impressive.

The initial system should be modular and maintainable, while remaining simple enough to develop locally.

## 5.8 Research area: Future avatar

Research realistic avatar architectures.

Potential areas to investigate include:

- Audio-driven facial animation.
- Lip synchronization.
- Face reenactment.
- Video generation.
- Real-time rendering.
- Virtual camera output.
- Integration with generated speech.

Candidate technologies may include MuseTalk, LivePortrait, or alternatives, but do not assume that any particular model is suitable.

Evaluate licensing, hardware requirements, latency, input requirements, output quality, and integration complexity.

Make it explicit that this research concerns a future capability unless the repository proves otherwise.

## 5.9 Research area: Security and privacy

Investigate the risks associated with:

- Meeting audio.
- Transcripts.
- Participant identities.
- Interview evaluations.
- API keys.
- Logs.
- Product knowledge bases.
- External model providers.
- Tool execution.
- Unauthorized actions.

The architecture must not assume that all participants have consented to recording or AI processing.

Document appropriate participant disclosure, authorization, retention, deletion, and access controls.

Avoid storing raw audio indefinitely without a clear requirement.

Avoid placing API keys, personal data, or sensitive transcripts in logs.

---

# PART 6: CREATE A COMPREHENSIVE DOCUMENTATION SYSTEM

Create a serious documentation folder.

Use the following structure as a starting point, adapting it to the existing repository when necessary.

Do not create empty documents just to satisfy the file list.

Each document must contain useful, specific, coherent information.

Recommended structure:

docs/
├── README.md
├── PRODUCT_VISION.md
├── PRODUCT_REQUIREMENTS.md
├── SYSTEM_ARCHITECTURE.md
├── COMPONENTS_AND_RESPONSIBILITIES.md
├── MEETING_CONTEXT_MODEL.md
├── MEETING_RUNTIME.md
├── PARTICIPANT_IDENTITY.md
├── SPEAKER_DIARIZATION.md
├── CONVERSATION_ENGINE.md
├── TURN_TAKING_AND_INTERRUPTION.md
├── AUDIO_PIPELINE.md
├── AI_PROVIDER_INTEGRATION.md
├── MEETING_PLATFORM_INTEGRATION.md
├── GOOGLE_MEET_RESEARCH.md
├── SPEECH_RECOGNITION_RESEARCH.md
├── SPEAKER_IDENTIFICATION_RESEARCH.md
├── VAD_AND_TURN_DETECTION_RESEARCH.md
├── GEMINI_RESEARCH.md
├── KOKORO_RESEARCH.md
├── REAL_TIME_ARCHITECTURE_RESEARCH.md
├── AVATAR_RESEARCH.md
├── SECURITY_AND_PRIVACY.md
├── FAILURE_MODES.md
├── TESTING_STRATEGY.md
├── OBSERVABILITY.md
├── LOCAL_DEVELOPMENT.md
├── CONFIGURATION.md
├── API_AND_EVENT_CONTRACTS.md
├── DATA_MODEL.md
├── IMPLEMENTATION_ROADMAP.md
├── ARCHITECTURAL_DECISIONS.md
├── GLOSSARY.md
└── research/
    ├── README.md
    ├── SOURCES.md
    ├── GOOGLE_MEET_OPTIONS.md
    ├── SPEECH_STACK_COMPARISON.md
    ├── DIARIZATION_OPTIONS.md
    ├── MODEL_SELECTION.md
    ├── AVATAR_OPTIONS.md
    └── OPEN_QUESTIONS.md

You may split or combine files when that improves clarity, but preserve the major areas of responsibility.

## Documentation quality requirements

Every substantial document should explain:

1. Purpose.
2. Scope.
3. Why the component exists.
4. Its responsibilities.
5. What it explicitly does not do.
6. Its inputs.
7. Its outputs.
8. Dependencies.
9. Interfaces with other components.
10. Important design decisions.
11. Failure modes.
12. Security and privacy considerations where applicable.
13. Testing requirements.
14. Current implementation status.
15. Open questions and unresolved decisions.

Use examples, diagrams, structured data, event flows, and pseudocode where they clarify the system.

Avoid repeating the same enormous architecture explanation in every file.

Use cross-links to the relevant documents.

Make documentation navigable from docs/README.md.

Use consistent terminology throughout.

Distinguish among:

- Implemented.
- Partially implemented.
- Designed but not implemented.
- Proposed.
- Researched but not selected.
- Unverified.
- Blocked by external access or hardware.
- Future work.

Never present a planned component as implemented.

---

# PART 7: DOCUMENT THE CONTEXT ENGINE

Create a detailed document explaining how the organizer's context becomes executable meeting behavior.

The context engine is one of the most important parts of Tara.

It should transform natural-language instructions into a validated, structured representation.

For example, the organizer might provide:

"Interview two candidates for a Go backend position. Ask about concurrency, database transactions, and API design. Let them take turns. Ask follow-up questions when needed. Don't interrupt. Produce separate assessments at the end."

The system should transform that into a structured configuration containing:

- Meeting purpose.
- Meeting mode.
- Objectives.
- Participant roles.
- Expected duration.
- Interview type.
- Topics.
- Question strategy.
- Turn-taking policy.
- Follow-up policy.
- Evaluation rubric.
- Speaking policy.
- Human takeover behavior.
- Tool permissions.
- Output requirements.
- Privacy settings.
- Constraints.

The exact schema should be versioned and documented.

Explain how the system should handle instructions that are:

- Ambiguous.
- Contradictory.
- Impossible.
- Missing required information.
- Outside the available capabilities.
- Unsafe or unauthorized.

The application should validate important configuration fields before a meeting starts.

Natural-language context should not directly grant arbitrary permissions or cause unrestricted tool execution.

Document the relationship between the organizer's instructions, application-enforced policies, model-generated decisions, and the runtime.

Include complete example configurations for:

1. Technical interview.
2. General interview.
3. Group interview.
4. Engineering meeting.
5. Product pitch.
6. Observation-only meeting.

---

# PART 8: DOCUMENT THE PARTICIPATION ENGINE

Create a detailed design for deciding when Tara should speak.

This must not be a vague instruction to "use AI to determine whether to speak."

Define the states, transitions, events, policies, and decision points.

Possible states include:

- INITIALIZING.
- CONNECTING.
- WAITING_FOR_MEETING.
- LISTENING.
- PROCESSING_SPEECH.
- IDENTIFYING_SPEAKER.
- EVALUATING_TURN.
- GENERATING_RESPONSE.
- VALIDATING_RESPONSE.
- WAITING_TO_SPEAK.
- SPEAKING.
- INTERRUPTED.
- YIELDING_CONTROL.
- RECOVERING.
- STOPPED.

You may revise this list based on the architecture.

Do not create states that add no meaningful behavior.

Document:

- What enters each state.
- What actions are permitted.
- What events cause transitions.
- What happens when a response becomes stale.
- How cancellation works.
- How human takeover works.
- How reconnection affects state.
- How duplicate events are handled.
- How the system avoids speaking twice for one event.

Distinguish between a proposed response and an approved action.

A model can suggest that Tara should respond, but the runtime must verify that speaking is still permitted.

Include pseudocode for the participation decision.

The pseudocode must consider:

- Direct address.
- Meeting mode.
- Current speaker.
- Whether another participant is speaking.
- Whether Tara is already speaking.
- Whether human takeover is active.
- Whether the request is within scope.
- Whether the speaker identity is reliable enough.
- Whether the response remains relevant.
- Whether the configured policy permits proactive participation.

Do not pretend that a simple deterministic rule can fully understand human conversation.

Explain which decisions are deterministic and which require semantic interpretation.

---

# PART 9: DOCUMENT SPEAKER IDENTITY PROPERLY

Create a detailed participant identity and speaker attribution design.

Explain these concepts separately:

1. Meeting participant identity.
2. Audio stream identity.
3. Diarization speaker labels.
4. Real-world identity.
5. Speaker-to-participant mapping.
6. Confidence and uncertainty.
7. Identity changes and mapping corrections.

Use examples showing how a diarization label can change or be incorrectly mapped.

Document how the system handles:

- Participants joining late.
- Participants leaving.
- Participants changing display names.
- A new speaker appearing.
- Multiple speakers talking simultaneously.
- Very short utterances.
- Similar voices.
- Noisy microphones.
- Speech recognition errors.
- Unknown participants.
- Incorrect identity associations.

Never equate a diarization label such as SPEAKER_00 with a person's actual identity without evidence.

If identity confidence is too low, the system must avoid actions that depend on knowing exactly who spoke.

For interviews, this is especially important because an incorrect association could corrupt the candidate evaluation.

Include testing scenarios that verify that Candidate A's response cannot silently be assigned to Candidate B.

---

# PART 10: DOCUMENT THE AUDIO PIPELINE

Explain the complete audio lifecycle:

1. Meeting audio acquisition.
2. Audio format validation.
3. Resampling where required.
4. Buffering.
5. Voice activity detection.
6. Speaker attribution.
7. Incremental or segmented transcription.
8. Transcript event generation.
9. Context updates.
10. Turn completion estimation.
11. Response generation.
12. Response validation.
13. Speech synthesis.
14. Audio output.
15. Interruption and cancellation.
16. Metrics and diagnostics.

Document audio formats, timestamps, buffering strategies, and cancellation behavior.

Where exact values are unknown, mark them as values to benchmark rather than inventing universal defaults.

Explain how the system should prevent:

- Audio feedback loops.
- Tara transcribing her own output as a new user utterance.
- Duplicate transcripts.
- Stale responses.
- Excessive buffering.
- Memory growth.
- Audio playback continuing after a human takeover.
- Incorrect speaker assignment.

Consider whether the meeting adapter can distinguish Tara's outgoing audio from participant audio.

If not, document the additional mechanisms required.

---

# PART 11: DEFINE INTERNAL CONTRACTS

The architecture must have clear interfaces.

Document proposed contracts for:

- MeetingAdapter.
- AudioInput.
- AudioOutput.
- SpeechRecognizer.
- SpeakerAttributor.
- VoiceActivityDetector.
- LanguageModelProvider.
- SpeechSynthesizer.
- ContextEngine.
- ConversationEngine.
- ParticipationEngine.
- MeetingRuntime.
- ToolExecutor.
- KnowledgeProvider.
- ReportGenerator.
- Future AvatarProvider.

These are logical contracts. Do not force every component into a separate process or microservice.

For each contract, define:

- Purpose.
- Inputs.
- Outputs.
- Error behavior.
- Cancellation behavior.
- Timeout expectations.
- Resource ownership.
- Observability requirements.

Use Python protocols or interfaces where appropriate after inspecting the existing codebase.

Prefer dependency injection and explicit contracts over deeply coupled implementations.

A future avatar should be implementable through a defined extension point without rewriting the core meeting runtime.

---

# PART 12: RESEARCH DOCUMENTATION REQUIREMENTS

The research phase must produce useful engineering decisions, not a list of links.

For each major technology, include:

- What it does.
- What Tara needs from it.
- Official source.
- Current availability.
- Licensing information when verified.
- Hardware requirements when verified.
- Integration complexity.
- Important limitations.
- Alternatives.
- Comparison against alternatives.
- Recommendation.
- Confidence in the recommendation.
- What still needs to be tested.

Create comparison tables where useful.

For example:

| Criterion | Option A | Option B | Implication for Tara |
|---|---|---|---|
| Local inference | Verified finding | Verified finding | Effect on privacy and deployment |
| Streaming | Verified finding | Verified finding | Effect on conversational latency |
| Apple Silicon | Verified finding | Verified finding | Effect on local development |
| Speaker identity | Verified finding | Verified finding | Effect on multi-person meetings |
| Licensing | Verified finding | Verified finding | Effect on distribution |
| Integration complexity | Evidence-based assessment | Evidence-based assessment | Implementation effort |

Do not fill this table with guesses.

If a field cannot be verified, write "Unknown; requires verification."

For major decisions, explain the evidence supporting the recommendation.

Use primary sources wherever possible.

Research documentation must include direct source links and access dates.

Where a source is outdated, identify the date and investigate whether a newer source exists.

Do not cite a search-result snippet when the underlying documentation is available.

Do not claim that you conducted an experiment unless you actually ran it.

---

# PART 13: CREATE THE MAIN README

After the product, architecture, and research documents exist, create an exceptional root README.md.

This is a hackathon-standard README that explains the product to developers, judges, collaborators, and technical reviewers.

It must be impressive but honest.

Use relevant icons and restrained emoji where appropriate.

Do not fill it with meaningless badges or fictional statistics.

Do not add badges for CI, coverage, deployments, or releases unless those systems actually exist.

Use this suggested structure:

1. Project name and tagline.
2. Product summary.
3. The problem.
4. The solution.
5. Why Tara is different.
6. Key capabilities.
7. Real-world use cases.
8. How Tara works.
9. Architecture diagram.
10. Example meeting workflows.
11. Technical stack.
12. Current implementation status.
13. Development roadmap.
14. Local development prerequisites.
15. Installation and setup.
16. Environment configuration.
17. How to run the application.
18. How to run tests.
19. Model download instructions for the relevant implementation phase.
20. Security and privacy.
21. Limitations.
22. Contributing.
23. Documentation index.
24. License, if an existing or confirmed license is present.
25. A concise closing statement.

Adapt sections to the actual repository and current implementation.

If setup cannot yet be performed because integrations have not been implemented, say so clearly.

Do not publish fictitious installation commands that do not match the actual project.

If a command is proposed for future implementation, label it accordingly.

## 13.1 Architecture diagram requirements

Create a substantial Mermaid architecture diagram that explains the whole system.

It should include, where relevant:

- Meeting platform.
- Meeting adapter.
- Meeting runtime.
- Audio input.
- Audio output.
- Voice activity detection.
- Speech recognition.
- Speaker diarization.
- Participant identity mapping.
- Context engine.
- Conversation engine.
- Participation engine.
- Gemini provider.
- Kokoro speech synthesis.
- Interview engine.
- Product knowledge provider.
- Tool execution.
- Report generation.
- Persistence.
- Logging and metrics.
- Configuration.
- Future avatar service.

Show the direction of data flow.

Distinguish control flow from audio and transcript flow where possible.

Use subgraphs to group meaningful boundaries.

Label local and external/cloud components.

Use different edge styles or labels where helpful.

Include a separate sequence diagram for a complete interaction.

For example:

1. The organizer provides context.
2. Tara validates and loads the context.
3. The meeting adapter joins the meeting.
4. A participant asks Tara a question.
5. Audio enters the pipeline.
6. The speaker is attributed.
7. The context is updated.
8. The participation engine approves a response.
9. Gemini generates the response.
10. The response is validated.
11. Kokoro generates audio.
12. The runtime checks whether speaking is still permitted.
13. Audio is transmitted through the supported meeting integration.
14. The runtime records the result.

Add another sequence diagram for interruption and human takeover.

Ensure that the Mermaid syntax is valid.

Do not make diagrams that imply unimplemented components already work.

Use labels such as "planned", "future", or "requires verification" when appropriate.

---

# PART 14: CREATE CLAUDE.md AND AGENTS.md

Create or update the appropriate project instruction files.

Before doing so, inspect whether either file already exists.

Preserve valuable existing instructions and reconcile conflicts.

Do not duplicate enormous architecture documents into the instruction files. Link to the relevant documentation instead.

## 14.1 CLAUDE.md

Create a practical CLAUDE.md for Claude Code.

It must explain:

- What Tara is.
- The current implementation scope.
- The key architectural principles.
- The relevant documentation entry points.
- The coding conventions.
- How to run tests and validation.
- How to research uncertain APIs.
- How to avoid destructive changes.
- How to report progress.
- How to avoid claiming work that was not performed.

Claude Code should read this file before implementing changes.

## 14.2 AGENTS.md

Create AGENTS.md with repository-wide engineering instructions that are useful to coding agents generally.

It should cover the following principles.

### Architecture rules

- Do not build a generic chatbot and call it Tara.
- Do not place the entire application in one enormous module.
- Do not combine meeting connectivity, AI reasoning, audio processing, and tool execution into one tightly coupled component.
- Do not introduce microservices without a clear technical justification.
- Do not create unnecessary abstractions that make the initial system harder to understand.
- Keep the application modular and testable.
- Use explicit interfaces between core components.
- Keep provider-specific code behind suitable interfaces.
- Keep meeting-platform-specific behavior behind a meeting adapter.
- Preserve the ability to replace models and providers.

### Coding rules

- Write readable, maintainable code.
- Use type annotations appropriately.
- Use clear names.
- Avoid giant functions.
- Avoid unexplained constants.
- Avoid duplicated business logic.
- Avoid hidden global state.
- Avoid circular dependencies.
- Avoid unnecessary dependencies.
- Do not silently swallow exceptions.
- Do not use broad exception handling without a clear recovery strategy.
- Do not use `async` for work that does not benefit from asynchronous execution.
- Do not block the event loop with CPU-heavy operations.
- Use appropriate worker processes or thread pools when justified.
- Handle cancellation correctly.
- Handle resource cleanup.
- Close audio streams and network connections properly.
- Use structured logging instead of uncontrolled print statements in production code.
- Never log API keys or sensitive raw audio.
- Use configuration rather than hardcoded secrets or environment-specific assumptions.

### AI integration rules

- Do not assume model output is correct.
- Validate structured model output.
- Handle malformed responses.
- Handle provider timeouts and failures.
- Use bounded retries where appropriate.
- Avoid uncontrolled retry loops.
- Avoid sending stale responses to the meeting.
- Separate model-generated suggestions from application-approved actions.
- Do not let model output directly execute arbitrary commands.
- Use explicit tool schemas and permission checks.
- Preserve uncertainty when speaker identity is ambiguous.
- Do not fabricate interview evidence or product capabilities.
- Keep provider model identifiers configurable.

### Audio and real-time rules

- Avoid unbounded audio queues.
- Define cancellation behavior.
- Define interruption behavior.
- Avoid audio feedback loops.
- Track timestamps where necessary.
- Avoid assuming that a pause always marks the end of a turn.
- Handle concurrent speech as a real limitation.
- Do not claim real-time performance without measurement.
- Measure latency across the complete pipeline.
- Test the behavior of stale and cancelled responses.

### Security rules

- Never commit secrets.
- Never hardcode API keys.
- Provide a safe `.env.example` if environment variables are used.
- Do not put real secrets in example files.
- Validate external inputs.
- Enforce permissions in application code.
- Do not allow untrusted meeting instructions to bypass application policy.
- Do not treat arbitrary model output as trusted instructions.
- Minimize sensitive information in logs.
- Document audio and transcript retention behavior.
- Use appropriate protections for meeting data.

### Testing rules

- Write unit tests for deterministic logic.
- Test state transitions.
- Test participant identity mapping.
- Test invalid and ambiguous contexts.
- Test interruption handling.
- Test cancellation.
- Test provider failures.
- Test timeouts.
- Test duplicate events.
- Test malformed model responses.
- Test meeting reconnection.
- Use fakes or mocks for external services when appropriate.
- Keep normal unit tests independent of live API credentials.
- Do not make the entire test suite require large model downloads.
- Separate offline tests, local-model tests, and live integration tests.
- Never claim a test passed without running it.
- Never disable a failing test simply to obtain a green result.

### Repository safety rules

- Inspect Git status before modifying files.
- Preserve unrelated user changes.
- Do not overwrite existing files blindly.
- Do not reset or discard user work.
- Do not commit secrets, model weights, or generated audio artifacts.
- Do not make unrelated refactors during focused tasks.
- Do not push changes or create releases unless explicitly authorized.
- Run formatting, linting, type checks, and tests appropriate to the changed code.
- Review the final diff.
- Update documentation when behavior or contracts change.

### Documentation rules

- Keep documentation synchronized with implementation.
- Label planned and implemented features accurately.
- Record important architecture decisions.
- Provide sources for research-derived claims.
- Avoid invented benchmarks.
- Avoid unsupported API claims.
- Document known limitations.
- Use Mermaid diagrams when they clarify architecture.
- Prefer actionable explanations over generic descriptions.

## 14.3 Instruction hierarchy

Explain how root-level instructions and any nested AGENTS.md files apply to their respective directories.

Avoid conflicting rules between CLAUDE.md and AGENTS.md.

Keep the files concise enough that agents can follow them consistently.

---

# PART 15: ARCHITECTURAL DECISION RECORDS

Create an architecture decision record system.

Use a directory such as:

docs/adr/

Include an index and initial ADRs for decisions that are important enough to record.

Potential ADR topics:

- Initial application architecture.
- Local versus cloud inference.
- Meeting integration strategy.
- Speaker attribution strategy.
- Audio pipeline design.
- Real-time event transport.
- Provider abstraction.
- State management.
- Future avatar extension.

Do not mark a decision as final merely because it appears on this list.

For each ADR, include:

- Title.
- Status.
- Context.
- Problem.
- Considered options.
- Decision.
- Reasons.
- Consequences.
- Risks.
- Evidence.
- Follow-up validation.

Use statuses such as proposed, accepted, superseded, and rejected.

If research is incomplete, the ADR should remain proposed.

---

# PART 16: IMPLEMENTATION ROADMAP

Create a practical, dependency-aware roadmap.

Do not organize the roadmap around impressive-sounding milestones that lack measurable outcomes.

The roadmap should explain the dependencies between components.

At minimum, investigate these phases.

## Phase 0: Architecture and technical verification

- Inspect the repository.
- Complete initial research.
- Establish documentation.
- Verify available Google Meet integration options.
- Identify the largest technical risks.
- Define testable acceptance criteria.

## Phase 1: Core meeting context and state

- Validate organizer context.
- Represent participants and roles.
- Maintain meeting state.
- Define event contracts.
- Implement deterministic speaking policies.
- Test state transitions.

## Phase 2: Local voice pipeline

- Integrate speech recognition.
- Integrate Gemini.
- Integrate Kokoro.
- Add VAD.
- Measure latency.
- Test cancellation and interruption.
- Document model installation and verification.

Do not download models until this phase is ready to begin.

## Phase 3: Speaker intelligence

- Implement or integrate diarization.
- Associate diarization labels with participant identities where evidence permits.
- Handle unknown speakers.
- Handle overlapping speech.
- Test individual and group attribution.

## Phase 4: Meeting integration

- Implement the verified meeting adapter.
- Prove actual two-way media communication.
- Handle lifecycle events.
- Test reconnects.
- Verify participant information access.
- Test human takeover.

Do not describe this phase as complete merely because a meeting URL can be opened.

## Phase 5: Interview engine

- Technical interviews.
- General interviews.
- Mixed interviews.
- Group interviews.
- Follow-up questions.
- Structured evaluations.
- Individual candidate reports.

## Phase 6: General meeting participation

- Direct addressing.
- Contextual responses.
- Proactive contributions.
- Observation-only mode.
- Decision and action summaries.
- Human takeover.

## Phase 7: Product pitch and knowledge grounding

- Product knowledge source.
- Approved product information.
- Retrieval and context selection where needed.
- Question answering.
- Objection handling.
- Uncertainty handling.
- Post-meeting reports.

## Phase 8: Future avatar

- Evaluate candidate models.
- Establish an avatar interface.
- Prototype audio synchronization.
- Evaluate hardware and latency.
- Integrate video output when feasible.

Revise the roadmap based on actual research and repository findings.

For every phase, define:

- Objective.
- Dependencies.
- Deliverables.
- Acceptance criteria.
- Test strategy.
- Risks.
- Exit criteria.

---

# PART 17: TESTING STRATEGY

Create a comprehensive testing strategy covering more than whether an API returns HTTP 200.

Include:

## Unit tests

- Context validation.
- Meeting state transitions.
- Participation decisions.
- Speaker mapping.
- Permission checks.
- Response validation.
- Event deduplication.

## Integration tests

- Gemini provider.
- Speech recognizer.
- Speech synthesizer.
- Audio processing.
- Meeting adapter.
- Persistence.
- Tool execution.

## Simulation tests

Create simulated conversations for:

- Directly addressing Tara.
- Talking about Tara without addressing her.
- Two people discussing a topic while Tara listens.
- An interview with one candidate.
- A group interview.
- A product pitch.
- An interruption.
- Human takeover.
- An unknown speaker.
- Conflicting instructions.
- An incorrect transcript.
- A speaker changing during an answer.
- A provider timeout.
- A disconnection.
- A delayed response that becomes stale.

## End-to-end tests

Define what must be verified when the real meeting integration is available.

Include measurable metrics such as:

- End-of-utterance to first audio response latency.
- Transcription latency.
- Speaker-attribution accuracy.
- False speaker attribution rate.
- False activation rate.
- Missed direct-address rate.
- Interruption response time.
- Stale-response delivery rate.
- Tool execution authorization failures.

Do not invent target values without explaining their basis.

Define initial baselines and establish realistic targets after benchmarking.

Separate simulated tests from real-meeting tests.

A mocked meeting adapter does not prove that Google Meet integration works.

---

# PART 18: FAILURE MODES AND RECOVERY

Create a detailed failure-mode document.

Consider:

- Google Meet connection failure.
- Meeting ended unexpectedly.
- Microphone permission denied.
- Audio input unavailable.
- Audio output unavailable.
- VAD incorrectly detects silence.
- Transcription fails.
- Speaker identity is uncertain.
- Gemini is unavailable.
- Gemini returns malformed output.
- Kokoro fails.
- Model loading fails.
- Local memory is insufficient.
- A response is generated too late.
- A participant interrupts Tara.
- A human takes over.
- Duplicate meeting events arrive.
- The meeting reconnects.
- Context conflicts with runtime state.
- A tool action is unauthorized.
- A product knowledge source is unavailable.
- A participant asks a question outside Tara's role.

For each case, explain:

- Detection.
- Immediate behavior.
- Recovery.
- Logging.
- User-visible consequences.
- Whether the system should continue, pause, or stop.

Fail safely.

For example, if Tara cannot identify a speaker confidently during a group interview, it should not assign a candidate-specific evaluation to an uncertain speaker.

If Gemini is unavailable, Tara should not pretend to have generated an intelligent response.

If the meeting integration disconnects, Tara should not assume that speech was delivered successfully.

---

# PART 19: LOCAL DEVELOPMENT DOCUMENTATION

Create a local setup guide specifically for the actual repository and Apple Silicon development environment.

Inspect the available project configuration before writing commands.

Document:

- Required Python version.
- Dependency installation.
- Virtual environment setup.
- Environment variables.
- Gemini API key configuration.
- Model directory configuration.
- Starting the backend.
- Running unit tests.
- Running integration tests.
- Checking model availability.
- Testing audio devices.
- Verifying the meeting adapter.
- Debugging common errors.

Separate the setup into stages.

### Stage A: Repository and development tools

Install only the dependencies needed to develop and test the basic application.

### Stage B: External API configuration

Explain how to configure Gemini credentials safely.

### Stage C: Local model preparation

Explain how to download the chosen models when implementation reaches the relevant phase.

Do not automatically download them during documentation generation.

### Stage D: Live audio testing

Explain how to test audio input, transcription, speech generation, and playback.

### Stage E: Meeting integration

Explain the verified setup for the selected meeting adapter.

Never write fake commands just to make the guide appear complete.

If the integration has not been implemented, clearly distinguish the planned procedure from working instructions.

---

# PART 20: CODE QUALITY AND HACKATHON STANDARD

This project must meet a high engineering standard even if the development timeline is short.

Hackathon quality does not mean using every technology or drawing the biggest architecture diagram.

It means the product has a clear purpose, a coherent design, a reliable core, reproducible setup, meaningful tests, understandable documentation, and an honest account of its capabilities.

The codebase should be easy for another engineer to understand and contribute to.

Prioritize:

- Clear module boundaries.
- Typed contracts.
- Predictable state transitions.
- Reliable cancellation.
- Proper resource management.
- Useful errors.
- Secure configuration.
- Meaningful logs.
- Testable business logic.
- Reproducible local development.
- Honest feature status.
- Clear architectural decisions.

Avoid:

- Overengineering.
- Premature microservices.
- Excessive abstraction.
- Dependency sprawl.
- Huge files containing unrelated responsibilities.
- Unbounded retries.
- Unbounded queues.
- Hidden background tasks.
- Silent exception handling.
- Hardcoded secrets.
- Arbitrary model names.
- Unverified external API assumptions.
- Fake test results.
- Fake benchmarks.
- Fake completion claims.

---

# PART 21: REQUIRED DELIVERABLES

At the end of this documentation and research phase, the repository should contain:

1. An accurate root README.md.
2. A comprehensive docs/ directory.
3. A product vision document.
4. A product requirements document.
5. A complete system architecture document.
6. A context engine design.
7. A meeting runtime design.
8. A participation and turn-taking design.
9. A speaker attribution design.
10. An audio pipeline design.
11. Integration contracts.
12. Google Meet integration research.
13. Speech recognition research.
14. Speaker diarization research.
15. VAD and turn-detection research.
16. Gemini integration research.
17. Kokoro research.
18. Real-time architecture research.
19. Avatar research.
20. Security and privacy documentation.
21. A failure-mode document.
22. A comprehensive testing strategy.
23. A local development guide.
24. An implementation roadmap.
25. Architectural decision records.
26. CLAUDE.md.
27. AGENTS.md.
28. A research source index.
29. An explicit list of unresolved technical questions.
30. A clear distinction between existing implementation and future work.

Create every document with meaningful content.

If a document is unnecessary because an equivalent, higher-quality document already exists, update or link to the existing document rather than creating duplicate documentation.

---

# PART 22: EXECUTION ORDER

Follow this sequence.

## Step 1: Inspect

Inspect the repository, current Git status, existing source files, dependencies, tests, and instructions.

Do not make destructive changes.

## Step 2: Establish the product baseline

Summarize your understanding of Tara, the current development scope, the intended architecture, the major unknowns, and the highest-risk integrations.

Keep this summary in your working notes and use it consistently throughout the documents.

## Step 3: Research

Research the external APIs, models, and integration approaches that determine whether the architecture is feasible.

Prioritize Google Meet media access, speaker identity, real-time audio, and local model performance.

Record the sources and findings.

## Step 4: Resolve architectural questions

Make recommendations supported by evidence.

Keep unverified decisions explicitly marked as proposed.

Do not pretend that every uncertainty can be resolved through research alone. Some questions require an implementation experiment.

## Step 5: Write the documentation

Create the documentation folder and its documents.

Write the architecture and contracts before documenting implementation details that depend on them.

Ensure the documents agree with each other.

## Step 6: Create the README

Create the root README after the architecture and research documents are sufficiently complete.

Include accurate Mermaid diagrams and realistic examples.

## Step 7: Create engineering instructions

Create or update CLAUDE.md and AGENTS.md.

Ensure they point to the authoritative documentation and do not contradict it.

## Step 8: Validate

Validate the documentation.

Check:

- All internal links.
- Mermaid syntax.
- File names.
- Architecture consistency.
- Component naming.
- API assumptions.
- Source links.
- Current implementation status.
- Installation commands.
- Configuration examples.
- Event names.
- Example schemas.
- Roadmap dependencies.

Do not claim a diagram was rendered or a link was verified unless you actually checked it.

Run lightweight validation scripts if useful and available.

Do not install large model dependencies just to validate documentation.

## Step 9: Final report

Provide a concise but useful summary of:

- Files created.
- Existing files modified.
- Major architecture decisions.
- Research findings.
- Sources that materially affected the design.
- Important unresolved questions.
- External integration blockers.
- What is actually implemented versus only documented.
- Recommended first implementation task.

Include the most important findings about Google Meet media integration.

Do not begin implementing the entire runtime after completing this phase unless explicitly instructed to continue.

---

# PART 23: FINAL OPERATING RULES

Keep these principles in mind throughout the task.

**Understand the repository before changing it.**

**Research before making claims about external integrations.**

**Document the system we are actually building, not a generic AI agent.**

**Treat the organizer's context as structured product input, not just a long prompt.**

**Treat meeting participation as a runtime policy problem, not simply text generation.**

**Treat speaker identity as an evidence-based association, not a guess.**

**Treat audio as a real-time stream with latency, cancellation, and resource constraints.**

**Treat external model responses as untrusted output that must be validated.**

**Treat human takeover as a first-class behavior.**

**Treat the avatar as an extension of the core system, not a prerequisite.**

**Treat Google Meet integration as a capability that must be verified, not assumed.**

**Treat local model installation as a later implementation step.**

**Never claim that something works unless it has actually been implemented and tested.**

**Never sacrifice technical accuracy for an impressive-looking README.**

The goal is to establish a foundation that a serious engineering team could use to build Tara, not merely to generate a collection of attractive Markdown files.

Begin by inspecting the repository.