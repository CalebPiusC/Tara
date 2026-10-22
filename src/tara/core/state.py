"""Typed conversation state, updated only by validated events from application code.

Transient state (floor, task, flags) is separate from anything persistable. A model
may *suggest* a topic or summary; it can never flip a critical flag. Retrieved context
is attached per question and is NOT part of live state flags or persistence.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .contracts import Passage, ToolCall
from .errors import InvalidStateChange

RUNTIME, MODEL = "runtime", "model"
STAGES = ("classifying", "routing", "retrieving", "generating", "validating", "waiting", "speaking")


@dataclass(frozen=True)
class UtteranceRecord:
    event_id: str
    speaker_id: str | None
    speaker_name: str | None
    confidence: float
    text: str
    ts_ms: int = 0
    overlap: bool = False


@dataclass(frozen=True)
class ActiveQuestion:
    event_id: str
    text: str
    asker_id: str | None
    asker_confidence: float
    answered: bool = False


@dataclass
class ResponseTask:
    trigger_event_id: str
    epoch: int
    stage: str = "classifying"
    route: str | None = None


# ---- events (the only way to change state) --------------------------------
@dataclass(frozen=True)
class UtteranceAdded:
    record: UtteranceRecord
    source: str = RUNTIME


@dataclass(frozen=True)
class QuestionAsked:
    question: ActiveQuestion
    source: str = RUNTIME


@dataclass(frozen=True)
class TopicSuggested:
    topic: str
    source: str = MODEL


@dataclass(frozen=True)
class SummaryUpdated:
    summary: str
    source: str = MODEL


@dataclass(frozen=True)
class ResponseStarted:
    trigger_event_id: str
    epoch: int
    source: str = RUNTIME


@dataclass(frozen=True)
class ResponseStage:
    stage: str
    route: str | None = None
    source: str = RUNTIME


@dataclass(frozen=True)
class ResponseEnded:
    trigger_event_id: str
    outcome: str  # completed | cancelled | failed | stale
    source: str = RUNTIME


@dataclass(frozen=True)
class SpeakingChanged:
    speaking: bool
    source: str = RUNTIME


@dataclass(frozen=True)
class Interrupted:
    source: str = RUNTIME


@dataclass(frozen=True)
class ControlChanged:
    user_has_control: bool
    source: str = RUNTIME


@dataclass(frozen=True)
class ToolQueued:
    call_id: str
    call: ToolCall
    source: str = RUNTIME


@dataclass(frozen=True)
class ToolFinished:
    call_id: str
    source: str = RUNTIME


@dataclass(frozen=True)
class ContextAttached:
    passages: tuple[Passage, ...]
    source: str = RUNTIME


StateEvent = (UtteranceAdded | QuestionAsked | TopicSuggested | SummaryUpdated | ResponseStarted | ResponseStage
              | ResponseEnded | SpeakingChanged | Interrupted | ControlChanged | ToolQueued | ToolFinished
              | ContextAttached)

_CRITICAL = (ResponseStarted, ResponseStage, ResponseEnded, SpeakingChanged, Interrupted, ControlChanged,
             ToolQueued, ToolFinished, ContextAttached, UtteranceAdded, QuestionAsked)


@dataclass
class ConversationState:
    meeting_id: str
    mode: str
    organizer_instructions: tuple[str, ...] = ()
    topic: str | None = None
    summary: str = ""
    recent: deque[UtteranceRecord] = field(default_factory=lambda: deque(maxlen=50))
    current_question: ActiveQuestion | None = None
    response_task: ResponseTask | None = None
    pending_tool_calls: dict[str, ToolCall] = field(default_factory=dict)
    tara_speaking: bool = False
    interrupted: bool = False
    user_has_control: bool = False
    retrieved_context: tuple[Passage, ...] = ()
    version: int = 0

    MAX_TOPIC = 80
    MAX_SUMMARY = 2000

    def apply(self, ev: StateEvent) -> None:
        if isinstance(ev, _CRITICAL) and ev.source != RUNTIME:
            raise InvalidStateChange(f"{type(ev).__name__} may only come from application code")
        if isinstance(ev, UtteranceAdded):
            self.recent.append(ev.record)
        elif isinstance(ev, QuestionAsked):
            self.current_question = ev.question
            self.retrieved_context = ()        # evidence belongs to one question
        elif isinstance(ev, TopicSuggested):
            t = " ".join(ev.topic.split())
            if not t or len(t) > self.MAX_TOPIC:
                raise InvalidStateChange("topic empty or too long")
            self.topic = t
        elif isinstance(ev, SummaryUpdated):
            if len(ev.summary) > self.MAX_SUMMARY:
                raise InvalidStateChange("summary too long")
            self.summary = ev.summary
        elif isinstance(ev, ResponseStarted):
            if self.user_has_control:
                raise InvalidStateChange("user has control; Tara may not start a response")
            if self.response_task is not None:
                raise InvalidStateChange("a response task is already active")
            self.response_task = ResponseTask(ev.trigger_event_id, ev.epoch)
            self.interrupted = False
        elif isinstance(ev, ResponseStage):
            if self.response_task is None:
                raise InvalidStateChange("no active response task")
            if ev.stage not in STAGES:
                raise InvalidStateChange(f"unknown stage {ev.stage!r}")
            self.response_task.stage = ev.stage
            if ev.route:
                self.response_task.route = ev.route
        elif isinstance(ev, ResponseEnded):
            if self.response_task is None or self.response_task.trigger_event_id != ev.trigger_event_id:
                raise InvalidStateChange("ending a response task that is not active")
            if ev.outcome == "completed" and self.current_question \
                    and self.current_question.event_id == ev.trigger_event_id:
                self.current_question = ActiveQuestion(**{**self.current_question.__dict__, "answered": True})
            self.response_task = None
            self.tara_speaking = False
        elif isinstance(ev, SpeakingChanged):
            if ev.speaking and self.user_has_control:
                raise InvalidStateChange("cannot speak while the user has control")
            self.tara_speaking = ev.speaking
        elif isinstance(ev, Interrupted):
            self.interrupted, self.tara_speaking = True, False
        elif isinstance(ev, ControlChanged):
            self.user_has_control = ev.user_has_control
            if ev.user_has_control:
                self.tara_speaking = False
        elif isinstance(ev, ToolQueued):
            if ev.call_id in self.pending_tool_calls:
                raise InvalidStateChange("duplicate tool call id")
            self.pending_tool_calls[ev.call_id] = ev.call
        elif isinstance(ev, ToolFinished):
            self.pending_tool_calls.pop(ev.call_id, None)
        elif isinstance(ev, ContextAttached):
            self.retrieved_context = ev.passages
        self.version += 1

    def to_persistable(self) -> dict[str, Any]:
        """Only what may outlive the meeting process. Flags, tasks, tool calls and raw retrieved
        passages are transient by design; durable memory is a separate system (docs/memory/MEMORY_AND_RAG.md)."""
        return {"meeting_id": self.meeting_id, "mode": self.mode, "topic": self.topic, "summary": self.summary}
