"""Structured expression events for a FUTURE avatar (none exists). Validated, rate-limited, sink-isolated.

Vocal expression (how Tara sounds) and visual expression (face/gesture) are separate concepts. These events
describe the conversation state and context-supported stance; they do NOT claim that the model's audio
contained a given emotion, because that cannot be measured from here.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Expression(str, Enum):
    SPEAKING_STARTED = "speaking_started"
    SPEAKING_STOPPED = "speaking_stopped"
    LISTENING = "listening"
    THINKING = "thinking"
    AMUSED = "amused"
    SURPRISED = "surprised"
    CONCERNED = "concerned"
    INTERRUPTED = "interrupted"
    YIELDING_CONTROL = "yielding_control"


EMOTIONAL = frozenset({Expression.AMUSED, Expression.SURPRISED, Expression.CONCERNED})
INTENSITIES = ("subtle", "moderate", "animated")
BASES = ("runtime", "context")


@dataclass(frozen=True)
class ExpressionEvent:
    kind: Expression
    ts_ms: int
    session_id: str
    response_id: int | None = None
    intensity: str = "subtle"
    basis: str = "runtime"     # runtime = derived from runtime state; context = inferred from conversation cues

    def validate(self) -> None:
        if not isinstance(self.kind, Expression):
            raise ValueError("kind must be an Expression")
        if self.intensity not in INTENSITIES:
            raise ValueError(f"intensity must be one of {INTENSITIES}")
        if self.basis not in BASES:
            raise ValueError(f"basis must be one of {BASES}")
        if self.ts_ms < 0 or not self.session_id:
            raise ValueError("ts_ms >= 0 and session_id required")
        if (self.kind in EMOTIONAL) != (self.basis == "context"):
            raise ValueError("emotional expressions need basis=context; state events need basis=runtime")
        if self.response_id is not None and self.response_id < 0:
            raise ValueError("response_id must be >= 0")


class ExpressionSink(Protocol):
    def emit(self, event: ExpressionEvent) -> None:
        """Must not block. A real avatar sink should queue the event."""
        ...


class NullExpressionSink:
    def emit(self, event: ExpressionEvent) -> None:
        return None


class ListExpressionSink:
    def __init__(self) -> None:
        self.events: list[ExpressionEvent] = []

    def emit(self, event: ExpressionEvent) -> None:
        self.events.append(event)


class ExpressionEmitter:
    def __init__(self, sink: ExpressionSink, session_id: str, clock: Callable[[], float] = time.monotonic,
                 emotion_min_interval_s: float = 5.0):
        self.sink, self.session_id, self._clock = sink, session_id, clock
        self.emotion_min_interval_s = emotion_min_interval_s
        self._last: Expression | None = None
        self._last_emotion_t = -1e9
        self.rejected = 0
        self.suppressed = 0
        self.sink_errors = 0

    def emit(self, kind: Expression, *, response_id: int | None = None, intensity: str = "subtle",
             basis: str | None = None) -> ExpressionEvent | None:
        basis = basis or ("context" if kind in EMOTIONAL else "runtime")
        now = self._clock()
        ev = ExpressionEvent(kind, int(now * 1000), self.session_id, response_id, intensity, basis)
        try:
            ev.validate()
        except ValueError:
            self.rejected += 1
            return None
        if kind in EMOTIONAL:
            if now - self._last_emotion_t < self.emotion_min_interval_s:
                self.suppressed += 1                         # no machine-gun reactions
                return None
            self._last_emotion_t = now
        elif kind is self._last:
            self.suppressed += 1                             # consecutive duplicate state
            return None
        if kind not in EMOTIONAL:
            self._last = kind
        try:
            self.sink.emit(ev)
        except Exception:  # noqa: BLE001 - an avatar problem must never reach the voice path
            self.sink_errors += 1
        return ev


def emotion_for(plan_regime: str, humour: str, laughter: bool, expressiveness: str, unexpected: bool,
                heavy: bool) -> Expression | None:
    """Context-supported stance for the moment Tara starts speaking, or None. Never invented from silence."""
    if expressiveness == "none":
        return None
    if heavy or plan_regime in ("empathy", "sensitive", "serious"):
        return Expression.CONCERNED
    if laughter and humour != "none":
        return Expression.AMUSED
    if unexpected:
        return Expression.SURPRISED
    return None
