"""Deterministic simulation harness: fake clock, scripted utterances with scripted
intents, fake LLM latency. Drives the real runtime/participation/evidence code.
It proves policy logic only; it says nothing about real audio, models, or Meet."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.config import MeetingConfig, Role
from ..floor.evidence import EvidenceStore
from ..floor.participation import (
    Action,
    Decision,
    Intent,
    IntentKind,
    Thresholds,
    Utterance,
    decide,
)
from ..floor.runtime import MeetingRuntime, Proposal, State


@dataclass
class Spoken:
    text: str
    trigger: str
    start_ms: int
    planned_ms: int
    delivered_ms: int | None = None  # None while playing
    outcome: str = "playing"  # playing | completed | interrupted | yielded


@dataclass
class Pending:
    proposal: Proposal
    ready_at_ms: int
    speak_ms: int


@dataclass
class FakeLLM:
    latency_ms: int = 800
    speak_ms: int = 4000
    calls: list[str] = field(default_factory=list)

    def propose(self, trigger_id: str, text: str) -> tuple[str, int, int]:
        self.calls.append(trigger_id)
        return f"reply to: {text}", self.latency_ms, self.speak_ms


class SimMeeting:
    def __init__(self, cfg: MeetingConfig, roles: dict[str, Role], th: Thresholds,
                 llm: FakeLLM | None = None, max_proposal_age_ms: int = 3000,
                 evidence_threshold: float | None = None):
        self.cfg, self.roles, self.th = cfg, roles, th
        self.llm = llm or FakeLLM()
        self.now = 0
        self.rt = MeetingRuntime(max_proposal_age_ms=max_proposal_age_ms)
        self.rt.transition(State.CONNECTING)
        self.rt.transition(State.LISTENING)
        cands = frozenset(p for p, r in roles.items() if r is Role.CANDIDATE)
        self.evidence = EvidenceStore(evidence_threshold or cfg.attribution_min_confidence or th.default_attribution, cands)
        self.spoken: list[Spoken] = []
        self.decisions: list[tuple[str, Decision]] = []
        self.rejected: list[tuple[str, str]] = []  # (trigger, reason)
        self._pending: Pending | None = None
        self._current: Spoken | None = None

    # -- time ----------------------------------------------------------
    def advance(self, to_ms: int) -> None:
        while True:
            nxt = self._next_deadline()
            if nxt is None or nxt > to_ms:
                break
            self.now = nxt
            self._fire()
        self.now = max(self.now, to_ms)

    def _next_deadline(self) -> int | None:
        c = []
        if self._pending:
            c.append(self._pending.ready_at_ms)
        if self._current and self._current.outcome == "playing":
            c.append(self._current.start_ms + self._current.planned_ms)
        return min(c) if c else None

    def _fire(self) -> None:
        if self._pending and self._pending.ready_at_ms == self.now:
            self._finish_generation()
        elif self._current and self._current.outcome == "playing" \
                and self.now >= self._current.start_ms + self._current.planned_ms:
            self._current.delivered_ms = self._current.planned_ms
            self._current.outcome = "completed"
            self.rt.transition(State.LISTENING)
            self._current = None

    def _finish_generation(self) -> None:
        p, self._pending = self._pending, None
        assert p is not None
        self.rt.transition(State.VALIDATING)
        ok = self.rt.approve(p.proposal, self.now)
        if not ok.approved:
            self.rejected.append((p.proposal.trigger_event_id, ok.reason))
            self.rt.transition(State.LISTENING)
            return
        self.rt.transition(State.WAITING_TO_SPEAK)
        self.rt.mark_answered(p.proposal.trigger_event_id)
        self.rt.transition(State.SPEAKING)
        self._current = Spoken(p.proposal.text, p.proposal.trigger_event_id, self.now, p.speak_ms)
        self.spoken.append(self._current)

    # -- input ---------------------------------------------------------
    def feed(self, at_ms: int, u: Utterance, intent: Intent) -> None:
        self.advance(at_ms)
        if not self.rt.accept_event(u.event_id):
            return  # duplicate adapter event
        self.evidence.add(u)

        if self.rt.state is State.SPEAKING:
            if decide(u, intent, self.rt, self.cfg, self.roles, self.th).action is Action.TAKEOVER:
                self._takeover(u)
                return
            self._stop_playback("interrupted")
            self.rt.on_barge_in()  # SPEAKING -> INTERRUPTED, epoch++
        elif self.rt.state in (State.GENERATING, State.VALIDATING) and intent.kind is IntentKind.HUMAN_TO_HUMAN:
            self.rt.bump_epoch()  # conversation moved on: pending proposal is now stale
            return

        if self.rt.state in (State.LISTENING, State.INTERRUPTED):
            self.rt.transition(State.EVALUATING_TURN)
        d = decide(u, intent, self.rt, self.cfg, self.roles, self.th)
        self.decisions.append((u.event_id, d))

        if d.action is Action.TAKEOVER:
            self._takeover(u)
        elif d.action is Action.ALLOW:
            self.rt.transition(State.GENERATING)
            text, lat, speak_ms = self.llm.propose(u.event_id, u.text)
            prop = Proposal(f"p_{u.event_id}", u.event_id, self.rt.epoch, self.now, text)
            self._pending = Pending(prop, self.now + lat, speak_ms)
        elif self.rt.state is State.EVALUATING_TURN:
            self.rt.transition(State.LISTENING)

    def _takeover(self, u: Utterance) -> None:
        self.decisions.append((u.event_id, Decision(Action.TAKEOVER, "takeover_phrase")))
        self._stop_playback("yielded")
        self._pending = None
        self.rt.start_takeover()

    def _stop_playback(self, outcome: str) -> None:
        if self._current and self._current.outcome == "playing":
            self._current.delivered_ms = self.now - self._current.start_ms
            self._current.outcome = outcome
            self._current = None

    def release_takeover(self) -> None:
        self.rt.release_takeover()
