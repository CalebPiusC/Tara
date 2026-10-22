# Internal Contracts and Events

Status: Proposed. Python `typing.Protocol`s; logical boundaries inside one process. Common rules: every async method accepts cancellation (raises `CancelledError`, never swallowed), has a documented timeout, owns and closes its resources, and emits metrics named `<component>.<op>.latency_ms`.

```python
class MeetingAdapter(Protocol):
    capabilities: AdapterCapabilities
    async def join(self, target: MeetingTarget) -> None: ...        # timeout: config; err: JoinError
    def events(self) -> AsyncIterator[MeetingEvent]: ...            # participant/lifecycle/audio
    async def send_audio(self, utt_id: str, chunks: AsyncIterator[PCMChunk]) -> DeliveryReceipt: ...
    async def stop_audio(self, utt_id: str) -> int: ...             # returns delivered_ms
    async def leave(self) -> None: ...
class VoiceActivityDetector(Protocol):
    def feed(self, stream_id: str, pcm16_16k: bytes, ts_ms: int) -> list[VadEvent]: ...
class SpeechRecognizer(Protocol):
    async def transcribe(self, seg: AudioSegment) -> Transcript: ...    # worker-backed
class SpeakerAttributor(Protocol):
    async def attribute(self, seg: AudioSegment, hints: list[SpeakerHint]) -> SpeakerAttribution: ...
class LanguageModelProvider(Protocol):
    async def generate(self, req: LlmRequest, schema: type[T]) -> T: ...   # timeout, bounded retry, validated
class SpeechSynthesizer(Protocol):
    def synthesize(self, text: str, voice: str) -> AsyncIterator[PCMChunk]: ...   # per sentence; cancel-safe
class ContextEngine(Protocol):
    async def build(self, raw: OrganizerInput) -> ConfigResult: ...    # validated config + issues
class ParticipationEngine(Protocol):
    def decide(self, utt: Utterance, view: RuntimeView) -> ParticipationDecision: ...   # sync, deterministic gate
class ConversationEngine(Protocol):
    async def propose(self, d: ParticipationDecision, view: RuntimeView) -> ResponseProposal: ...
class ToolExecutor(Protocol):
    async def execute(self, call: ToolCall, grant: ToolGrant) -> ToolResult: ...   # permission checked here, not by caller
class KnowledgeProvider(Protocol):
    async def search(self, query: str, k: int) -> list[Passage]: ...
class ReportGenerator(Protocol):
    async def generate(self, meeting_id: str, kind: ReportKind) -> Report: ...
class AvatarProvider(Protocol):  # future; NullAvatarProvider default
    async def on_speech_start(self, utt_id: str, planned_ms: int) -> None: ...
    async def on_audio(self, utt_id: str, chunk: PCMChunk, offset_ms: int) -> None: ...
    async def on_interrupt(self, utt_id: str, at_ms: int) -> None: ...
    async def on_state(self, state: str) -> None: ...
```
`MeetingRuntime` is a concrete class (not a plug-in) that wires these via constructor injection.

## Events (names are stable identifiers)
`meeting.joined`, `meeting.left`, `meeting.reconnected`, `participant.joined|left|renamed`, `audio.frame` (internal, not logged), `vad.speech_started|speech_ended`, `transcript.final`, `attribution.updated`, `participation.decided`, `proposal.created|rejected|approved|stale`, `speech.synthesized|playback_started|delivered|interrupted|failed`, `takeover.started|released`, `tool.requested|denied|executed`, `runtime.state_changed`, `report.ready`. Each carries `event_id`, `meeting_id`, `epoch`, `ts_ms`.
