"""Channel -> speaker binding by energy/glow correlation, for Google Meet's per-participant audio.

Meet renders every remote participant as its own media element with a live `MediaStream`, so the audio is already separated by
person. What is missing is the *link* between a channel and a name: the elements are an anonymous, rotating pool, and the "who is
lit" glow is global, so a channel that is simply the loudest at a glow onset can be the wrong person (the cross-channel name leak).

The link this module computes is correlation over time instead of a single onset: a channel belongs to the tile that is lit
exactly while that channel is loud, and not otherwise. Because a tile lights up when its own speaker is loud, and a channel is
loud when its own speaker talks, the two signals agree even under constant overlap; brief solo moments separate them. A name is
only attached when the evidence passes a floor and no other channel correlates with the same tile more strongly; otherwise the
channel stays UNKNOWN. Nothing here guesses, and nothing here touches audio or the DOM: callers feed it glow edges and one
(frame) energy reading per channel, and it answers who that channel is right now.

Derived from Vexa's `core/meetings/modules/gmeet-capture/src/gmeet-channel-binder.ts` (Apache-2.0, Vexa-ai/vexa); the derivation
and the deliberate changes are recorded in `docs/google-meet/SPEAKER_BINDING.md`. No Vexa code is imported at runtime and no Vexa
service is required: this is the algorithm, re-expressed in Python for Tara's adapter.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_TAU_MS = 2500.0            # how long one agreement keeps counting (the correlation window)
DEFAULT_LOUD_THRESHOLD = 0.02      # peak |sample| above which a channel counts as actively speaking
DEFAULT_MIN_SCORE = 2.5            # decayed agreement needed before a name is attached at all
DEFAULT_MARGIN = 1.5               # how far the best name must lead the runner-up before it is used at all
DEFAULT_RECENT_MS = 800.0          # a tile dark for longer than this stops being a candidate for any channel
MAX_CHANNELS = 64                  # a bounded table: a Meet call never has more live channels than this
MAX_NAMES_PER_CHANNEL = 32         # and no channel can be compared against more names than this


@dataclass(frozen=True)
class BinderConfig:
    """Thresholds, not tuning knobs to be guessed at: Vexa's live-tested values, Tara's stricter margin, and the recency rule."""

    tau_ms: float = DEFAULT_TAU_MS
    loud_threshold: float = DEFAULT_LOUD_THRESHOLD
    min_score: float = DEFAULT_MIN_SCORE
    margin: float = DEFAULT_MARGIN
    recent_ms: float | None = DEFAULT_RECENT_MS   # None disables the rule (the reference binder's behaviour)


def pick_bound_name(lit_names: Sequence[str]) -> str | None:
    """The name to stamp on a chunk captured right now: exactly one tile lit means that person; zero or two-plus means UNKNOWN.

    Overlap is ambiguous at the source, so a chunk captured while two people are lit carries no name. The correlation binder
    resolves the channel later; this function is only for stamping an individual frame.
    """
    return lit_names[0] if len(lit_names) == 1 else None


class ChannelBinder:
    """Learns which participant name each audio channel carries, and refuses to answer when the evidence is weak.

    One instance per meeting. `record_glow` is fed Meet's speaking edges, `name_for_channel` is called once per audio frame with
    that channel's peak amplitude, and the return value is the current speaker for that channel or None (UNKNOWN).

    Times are milliseconds on one monotonic clock shared by both inputs (the page's `Date.now()` in production). Feeding the two
    inputs from different clocks makes the decay meaningless, so the caller owns that contract.
    """

    def __init__(self, config: BinderConfig | None = None, *, self_name: str | None = None):
        self.config = config or BinderConfig()
        if self.config.tau_ms <= 0 or self.config.min_score <= 0 or self.config.loud_threshold < 0 or self.config.margin < 1:
            raise ValueError("binder thresholds must be positive (margin >= 1)")
        if self.config.recent_ms is not None and self.config.recent_ms < 0:
            raise ValueError("recent_ms must be positive or None")
        self._glowing: set[str] = set()
        self._agree: dict[int, dict[str, tuple[float, float]]] = {}      # channel -> name -> (score, ts)
        self._last_glow: dict[str, float] = {}                           # name -> when its tile last lit or went dark
        self._self_name: str | None = None
        if self_name:
            self.set_self_name(self_name)

    @property
    def self_name(self) -> str | None:
        """The local participant (the bot). Sticky: once known it can never be bound to a remote channel."""
        return self._self_name

    @property
    def glowing(self) -> tuple[str, ...]:
        """Who Meet currently renders as speaking, as last reported by `record_glow`."""
        return tuple(sorted(self._glowing))

    def set_self_name(self, name: str | None) -> None:
        """Pin the local participant's name. Also purges any agreement it already accrued.

        The marker that names the local tile can render late, so the bot's own name may have leaked into the table before it was
        known. Purging is what turns such a leak back into UNKNOWN instead of leaving a wrong name attached.
        """
        self._self_name = name or None
        if not self._self_name:
            return
        self._glowing.discard(self._self_name)
        self._last_glow.pop(self._self_name, None)
        for by_name in self._agree.values():
            by_name.pop(self._self_name, None)

    def record_glow(self, name: str, is_end: bool, ts_ms: float) -> None:
        """A glow edge for a tile: `is_end=False` is an onset, `True` is an offset. The local participant never glows-to-bind."""
        name = name.strip()
        if not name or name == self._self_name:
            return
        if is_end:
            self._glowing.discard(name)
        else:
            self._glowing.add(name)
        self._last_glow[name] = ts_ms

    def name_for_channel(self, channel: int, ts_ms: float, energy: float) -> str | None:
        """Record this channel's energy at `ts_ms` and return its current speaker, or None when it is not earned."""
        if channel < 0 or channel >= MAX_CHANNELS or not math.isfinite(energy):
            return None
        if energy > self.config.loud_threshold and self._glowing:
            by_name = self._agree.get(channel)
            if by_name is None:
                if len(self._agree) >= MAX_CHANNELS:
                    return None
                by_name = self._agree[channel] = {}
            for name in self._glowing:                                   # this channel is loud while `name` glows: +1 evidence
                if name not in by_name and len(by_name) >= MAX_NAMES_PER_CHANNEL:
                    continue
                by_name[name] = (self._current(channel, name, ts_ms) + 1.0, ts_ms)
        return self._assign(channel, ts_ms)

    def scores(self, channel: int, ts_ms: float) -> dict[str, float]:
        """The decayed evidence per name for one channel. Diagnostics and tests only; nothing decides on this."""
        by_name = self._agree.get(channel) or {}
        return {name: self._current(channel, name, ts_ms) for name in by_name}

    def reset(self) -> None:
        """Forget everything except the sticky self name (used when a page is replaced on reconnect)."""
        self._glowing.clear()
        self._agree.clear()
        self._last_glow.clear()

    # ---- internals ------------------------------------------------------------------------------------------------
    def _current(self, channel: int, name: str, ts_ms: float) -> float:
        entry = self._agree.get(channel, {}).get(name)
        if entry is None:
            return 0.0
        score, ts = entry
        elapsed = max(0.0, float(ts_ms) - ts)                            # a clock that steps back must not amplify old evidence
        return score * math.exp(-elapsed / self.config.tau_ms)

    def _assign(self, channel: int, ts_ms: float) -> str | None:
        by_name = self._agree.get(channel)
        if not by_name:
            return None
        best: str | None = None
        best_score = 0.0
        runner_up = 0.0
        for name in by_name:
            if name == self._self_name:                                  # the bot's own name never wins a remote channel
                continue
            if not self._recent(name, ts_ms):                            # a tile dark for too long is not a candidate at all
                continue
            score = self._current(channel, name, ts_ms)
            if score > best_score:
                best, best_score, runner_up = name, score, best_score
            elif score > runner_up:
                runner_up = score
        if best is None or best_score < self.config.min_score:
            return None                                                  # not enough evidence: UNKNOWN, never a guess
        if runner_up > 0.0 and best_score < runner_up * self.config.margin:
            return None                                                  # two names fit this channel about equally: no claim
        # One tile belongs to one channel: claim `best` only when every other channel correlates with it strictly less.
        for other in self._agree:
            if other != channel and self._current(other, best, ts_ms) >= best_score:
                return None
        return best

    def _recent(self, name: str, ts_ms: float) -> bool:
        """Whether `name` may be claimed at all right now.

        A tile that is lit is always current. A tile that has gone dark is still current for `recent_ms`, which covers the page's
        polling interval and the glow's own lag; after that its evidence belongs to an earlier turn, and a channel Meet has since
        handed to somebody else must not keep the old owner's name. Disabling the rule restores the reference binder's behaviour.
        """
        if self.config.recent_ms is None or name in self._glowing:
            return True
        last = self._last_glow.get(name)
        return last is not None and float(ts_ms) - last <= self.config.recent_ms
