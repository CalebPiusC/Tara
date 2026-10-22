"""AutoMemory: applies the tier decision to each finished participant turn.

- SESSION tier  -> a candidate note in this meeting's memory (provenance is the turn id)
- LONG_TERM tier-> a long-term memory CANDIDATE with an expiry; seen again, or confirmed, it becomes permanent
- EPHEMERAL     -> nothing is stored; sensitive turns are counted, never logged or stored

It runs after the turn is complete (never on the audio path), never raises into the voice engine, and writes only
what the participant said, tagged with who said it (or skipped when the speaker is unknown and the fact is personal).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from datetime import timedelta

from ..core.metrics import Metrics
from ..memory.models import MemoryScope, utcnow
from ..memory.service import MemoryService
from ..voice.memory import NOTE_KINDS, MeetingMemory, MemoryRecord, Scope
from ..voice.turns import TurnRecord
from .tiering import Tier, TierDecision, classify_turn

MODES = ("off", "candidate")
__all__ = ["MODES", "AutoMemory", "AutoMemoryConfig", "TurnRecord"]


@dataclass(frozen=True)
class AutoMemoryConfig:
    mode: str = "candidate"
    long_term_ttl_days: int = 30
    promote_after_sightings: int = 2
    max_text_chars: int = 500


class AutoMemory:
    def __init__(self, cfg: AutoMemoryConfig, *, session_id: str, scope: Scope, meeting_memory: MeetingMemory | None,
                 memory_service: MemoryService | None = None, memory_scope: MemoryScope | None = None,
                 metrics: Metrics | None = None, enabled_session: bool = True):
        if cfg.mode not in MODES:
            raise ValueError(f"unknown auto-memory mode {cfg.mode!r}; choose from {list(MODES)}")
        self.cfg, self.session_id, self.scope = cfg, session_id, scope
        self.meeting_memory, self.service, self.memory_scope = meeting_memory, memory_service, memory_scope
        self.metrics = metrics or Metrics()
        self.enabled_session = enabled_session
        self._seq = itertools.count(1)

    @property
    def active(self) -> bool:
        return self.cfg.mode != "off"

    async def observe(self, turn: TurnRecord) -> TierDecision:
        """Never raises. Returns the decision that was applied (for tests and metrics)."""
        d = classify_turn(turn.text)
        if not self.active:
            return d
        try:
            if d.reason == "sensitive":
                self.metrics.inc("auto_memory_skipped_sensitive")
            elif d.tier is Tier.SESSION and self.enabled_session and self.meeting_memory is not None:
                self._save_session(turn, d)
            elif d.tier is Tier.LONG_TERM and self.service is not None and self.memory_scope is not None:
                await self._save_long_term(turn, d)
            else:
                self.metrics.inc("auto_memory_ephemeral")
        except Exception:  # noqa: BLE001 - memory trouble must never reach the live conversation
            self.metrics.inc("auto_memory_failures")
        return d

    def _save_session(self, turn: TurnRecord, d: TierDecision) -> None:
        if d.kind not in NOTE_KINDS:
            return
        text = turn.text.strip()[: self.cfg.max_text_chars]
        who = turn.speaker_name if turn.speaker_name and turn.reliable else ""
        body = f"{who}: {text}" if who else text
        self.meeting_memory.add(  # type: ignore[union-attr]
            self.scope, MemoryRecord(d.kind, body, (f"{self.session_id}:auto:{turn.turn_id}:{next(self._seq)}",),
                                     owner=who if d.kind == "action_item" else ""))
        self.metrics.inc("auto_memory_session_saved")

    async def _save_long_term(self, turn: TurnRecord, d: TierDecision) -> None:
        assert self.service is not None and self.memory_scope is not None
        speaker = turn.speaker_name if turn.speaker_name and turn.reliable else ""
        if d.needs_speaker and not speaker:
            self.metrics.inc("auto_memory_skipped_unknown_speaker")      # a personal fact must be attributable
            return
        text = turn.text.strip()[: self.cfg.max_text_chars]
        body = f"{speaker} said: {text}" if speaker else text
        store = self.service.store
        existing = await store.find_exact(self.memory_scope, body)
        if existing is not None and existing.status != "superseded":
            sightings = int(existing.metadata.get("sightings", 1)) + 1
            promoted = existing.status == "candidate" and sightings >= self.cfg.promote_after_sightings
            updated = replace(existing, updated_at=utcnow(), metadata={**existing.metadata, "sightings": sightings},
                              status="confirmed" if promoted else existing.status,
                              expires_at=None if promoted else existing.expires_at)
            await store.update(updated)
            self.metrics.inc("auto_memory_promoted" if promoted else "auto_memory_reinforced")
            return
        expires = utcnow() + timedelta(days=self.cfg.long_term_ttl_days)
        await self.service.remember(
            self.memory_scope, body, source=f"auto:{self.session_id}:{turn.turn_id}", kind=d.kind, session_id=self.session_id,
            expires_at=expires, confidence=0.6, status="candidate",
            metadata={"auto": True, "sightings": 1, "speaker": speaker or "unknown", "reason": d.reason})
        self.metrics.inc("auto_memory_long_term_candidates")
