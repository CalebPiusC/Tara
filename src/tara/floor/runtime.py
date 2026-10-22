"""MeetingRuntime state machine: owns state, epoch, takeover, dedupe, approvals.

Models propose; only this module approves speech. See docs/turn-taking/MEETING_RUNTIME.md.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    INITIALIZING = "INITIALIZING"
    CONNECTING = "CONNECTING"
    LISTENING = "LISTENING"
    EVALUATING_TURN = "EVALUATING_TURN"
    GENERATING = "GENERATING"
    VALIDATING = "VALIDATING"
    WAITING_TO_SPEAK = "WAITING_TO_SPEAK"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    YIELDING_CONTROL = "YIELDING_CONTROL"
    RECOVERING = "RECOVERING"
    STOPPED = "STOPPED"


S = State
TRANSITIONS: dict[State, frozenset[State]] = {
    S.INITIALIZING: frozenset({S.CONNECTING, S.STOPPED}),
    S.CONNECTING: frozenset({S.LISTENING, S.RECOVERING, S.STOPPED}),
    S.LISTENING: frozenset({S.EVALUATING_TURN, S.RECOVERING, S.STOPPED}),
    S.EVALUATING_TURN: frozenset({S.GENERATING, S.LISTENING, S.RECOVERING, S.STOPPED}),
    S.GENERATING: frozenset({S.VALIDATING, S.LISTENING, S.RECOVERING, S.STOPPED}),
    S.VALIDATING: frozenset({S.WAITING_TO_SPEAK, S.LISTENING, S.RECOVERING, S.STOPPED}),
    S.WAITING_TO_SPEAK: frozenset({S.SPEAKING, S.LISTENING, S.YIELDING_CONTROL, S.RECOVERING, S.STOPPED}),
    S.SPEAKING: frozenset({S.LISTENING, S.INTERRUPTED, S.YIELDING_CONTROL, S.RECOVERING, S.STOPPED}),
    S.INTERRUPTED: frozenset({S.EVALUATING_TURN, S.YIELDING_CONTROL, S.RECOVERING, S.STOPPED}),
    S.YIELDING_CONTROL: frozenset({S.LISTENING, S.RECOVERING, S.STOPPED}),
    S.RECOVERING: frozenset({S.LISTENING, S.STOPPED}),
    S.STOPPED: frozenset(),
}


class IllegalTransition(Exception):
    pass


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    trigger_event_id: str
    epoch: int
    created_ms: int
    text: str


@dataclass(frozen=True)
class Approval:
    approved: bool
    reason: str = "ok"


class MeetingRuntime:
    """Single-writer runtime. Not thread-safe; call from the event loop only."""

    def __init__(self, max_proposal_age_ms: int, dedupe_window: int = 1024, policy_silent: bool = False):
        if max_proposal_age_ms <= 0:
            raise ValueError("max_proposal_age_ms must be positive")
        self.state = State.INITIALIZING
        self.epoch = 0
        self.takeover_active = False
        self.policy_silent = policy_silent
        self.others_speaking: set[str] = set()
        self.max_proposal_age_ms = max_proposal_age_ms
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._dedupe_window = dedupe_window
        self._answered: OrderedDict[str, None] = OrderedDict()  # bounded like _seen

    # -- state ---------------------------------------------------------
    def transition(self, to: State) -> None:
        if to not in TRANSITIONS[self.state]:
            raise IllegalTransition(f"{self.state.value} -> {to.value}")
        self.state = to

    # -- dedupe --------------------------------------------------------
    def accept_event(self, event_id: str) -> bool:
        """True if first sighting. Bounded memory (oldest ids evicted)."""
        if event_id in self._seen:
            return False
        self._seen[event_id] = None
        while len(self._seen) > self._dedupe_window:
            self._seen.popitem(last=False)
        return True

    # -- epoch / control -----------------------------------------------
    def bump_epoch(self) -> int:
        self.epoch += 1
        return self.epoch

    def start_takeover(self) -> None:
        """Close the speech path. Caller cancels playback/LLM work."""
        self.takeover_active = True
        self.bump_epoch()
        if self.state in (S.SPEAKING, S.WAITING_TO_SPEAK, S.INTERRUPTED):
            self.transition(S.YIELDING_CONTROL)
        elif self.state in (S.EVALUATING_TURN, S.GENERATING, S.VALIDATING):
            self.transition(S.LISTENING)  # idle takeover just sets the flag

    def release_takeover(self) -> None:
        if not self.takeover_active:
            return
        self.takeover_active = False
        if self.state is S.YIELDING_CONTROL:
            self.transition(S.LISTENING)

    def on_barge_in(self) -> None:
        """Human speech while Tara speaks. Caller cancels TTS/playback."""
        if self.state is S.SPEAKING:
            self.bump_epoch()
            self.transition(S.INTERRUPTED)

    def on_reconnect(self) -> None:
        self.bump_epoch()
        if self.state is not S.STOPPED:
            self.state = S.RECOVERING  # reconnect is legal from any live state

    # -- approval ------------------------------------------------------
    def approve(self, p: Proposal, now_ms: int) -> Approval:
        """Re-validate a model proposal against *current* state."""
        if self.state is S.STOPPED:
            return Approval(False, "stopped")
        if self.policy_silent:
            return Approval(False, "policy_silent")
        if self.takeover_active:
            return Approval(False, "takeover")
        if p.epoch != self.epoch:
            return Approval(False, "stale_epoch")
        if now_ms - p.created_ms > self.max_proposal_age_ms:
            return Approval(False, "stale_age")
        if p.trigger_event_id in self._answered:
            return Approval(False, "duplicate_trigger")
        if self.others_speaking:
            return Approval(False, "floor_busy")
        return Approval(True)

    def mark_answered(self, trigger_event_id: str) -> None:
        self._answered[trigger_event_id] = None
        while len(self._answered) > self._dedupe_window:
            self._answered.popitem(last=False)
