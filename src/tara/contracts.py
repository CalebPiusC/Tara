"""Typed data and Protocol contracts (docs/API_AND_EVENT_CONTRACTS.md).

Logical boundaries inside one process; nothing here implies separate services.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, TypeVar

T = TypeVar("T")


# ---- audio ---------------------------------------------------------------
@dataclass(frozen=True)
class PCMChunk:
    """Mono PCM16 little-endian."""

    data: bytes
    sample_rate: int
    ts_ms: int
    stream_id: str = "mix"

    @property
    def duration_ms(self) -> int:
        return int(len(self.data) / 2 / self.sample_rate * 1000)


@dataclass(frozen=True)
class AudioSegment:
    stream_id: str
    pcm: bytes
    sample_rate: int
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class Transcript:
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None
    language: str | None = None


# ---- speakers ------------------------------------------------------------
class HintKind(str, Enum):
    STREAM = "stream"                # per-participant audio stream (strong)
    ACTIVE_SPEAKER = "active_speaker"  # platform indicator (weak, laggy)
    DIARIZATION = "diarization"      # anonymous cluster label
    SELF_INTRO = "self_intro"        # human-confirmed introduction


@dataclass(frozen=True)
class SpeakerHint:
    kind: HintKind
    ts_ms: int
    participant_id: str | None = None
    label: str | None = None
    score: float = 1.0


@dataclass(frozen=True)
class SpeakerAttribution:
    participant_id: str | None
    confidence: float
    overlap: bool = False
    evidence: tuple[str, ...] = ()
    mapping_version: int = 0
    candidates: tuple[tuple[str, float], ...] = ()


# ---- meeting events --------------------------------------------------------
class MeetingEventKind(str, Enum):
    JOINED = "meeting.joined"
    LEFT = "meeting.left"
    RECONNECTED = "meeting.reconnected"
    ENDED = "meeting.ended"
    PARTICIPANT_JOINED = "participant.joined"
    PARTICIPANT_LEFT = "participant.left"
    PARTICIPANT_RENAMED = "participant.renamed"
    AUDIO = "audio.frame"
    SPEAKER_HINT = "speaker.hint"


@dataclass(frozen=True)
class MeetingEvent:
    kind: MeetingEventKind
    ts_ms: int
    event_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterCapabilities:
    per_participant_audio: bool = False
    participant_events: bool = True
    send_audio: bool = True
    send_video: bool = False
    delivery_confirmation: bool = False
    can_mute_self: bool = False       # platform-level microphone mute of Tara herself
    can_camera: bool = False
    can_show_image: bool = False      # display a still picture as Tara's identity
    can_mute_others: bool = False     # never enabled by default; needs explicit meeting config too


@dataclass(frozen=True)
class ActionOutcome:
    """Result of a platform action (mute, camera, picture). `ok=False` is reported, never assumed away."""

    ok: bool
    reason: str = ""


@dataclass(frozen=True)
class MeetingTarget:
    url: str = ""
    label: str = ""


@dataclass(frozen=True)
class DeliveryReceipt:
    utterance_id: str
    delivered_ms: int
    completed: bool


# ---- LLM -----------------------------------------------------------------
@dataclass(frozen=True)
class LlmRequest:
    system: str
    user: str
    temperature: float = 0.3
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class Passage:
    id: str
    text: str
    source: str = ""


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: Any = None
    error: str | None = None
    pending_approval: bool = False
    untrusted: bool = True   # tool output is data, never instructions


# ---- protocols -------------------------------------------------------------
class MeetingAdapter(Protocol):
    capabilities: AdapterCapabilities

    async def join(self, target: MeetingTarget) -> None: ...
    def events(self) -> AsyncIterator[MeetingEvent]: ...
    async def send_audio(self, utterance_id: str, chunks: AsyncIterator[PCMChunk]) -> DeliveryReceipt: ...
    async def stop_audio(self, utterance_id: str) -> int: ...
    async def leave(self) -> None: ...


class VoiceActivity(Protocol):
    """Frame classifier. Frames are PCM16 mono at `sample_rate`."""

    frame_ms: int
    sample_rate: int

    def is_speech(self, frame: bytes) -> bool: ...


class SpeechRecognizer(Protocol):
    async def transcribe(self, seg: AudioSegment) -> Transcript: ...


class SpeechSynthesizer(Protocol):
    sample_rate: int

    def synthesize(self, text: str) -> AsyncIterator[PCMChunk]: ...


class LanguageModelProvider(Protocol):
    async def generate(self, req: LlmRequest, parse: Callable[[Any], T], schema: dict[str, Any] | None = None) -> T:
        """Returns parse(json). Raises ProviderTimeout/ProviderError/MalformedOutput."""
        ...


class KnowledgeProvider(Protocol):
    async def search(self, query: str, k: int = 3) -> list[Passage]: ...


class AvatarProvider(Protocol):
    async def on_speech_start(self, utterance_id: str, planned_ms: int) -> None: ...
    async def on_audio(self, utterance_id: str, chunk: PCMChunk, offset_ms: int) -> None: ...
    async def on_interrupt(self, utterance_id: str, at_ms: int) -> None: ...
    async def on_state(self, state: str) -> None: ...


class NullAvatarProvider:
    async def on_speech_start(self, utterance_id: str, planned_ms: int) -> None: ...
    async def on_audio(self, utterance_id: str, chunk: PCMChunk, offset_ms: int) -> None: ...
    async def on_interrupt(self, utterance_id: str, at_ms: int) -> None: ...
    async def on_state(self, state: str) -> None: ...
