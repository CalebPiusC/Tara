"""Score the channel binder against ground truth: the offline attribution ladder.

`binding.py` decides whether a channel may carry a name. That decision can be wrong in two directions, and only one of them is
harmful: staying UNKNOWN costs a label, while attaching the wrong person's name is a false statement the engine may act on. A
change to the thresholds therefore has to be judged on both, not on the one meeting that motivated it.

This module replays a scenario — who really spoke on which channel and when (ground truth), plus the glow edges Meet actually
showed (the observed hint, which may lag, flicker or be missing) — through the real `ChannelBinder` on a virtual clock, then scores
the answers. It is the naming counterpart of Vexa's transcript judge (`core/meetings/eval/src/judge.py`) and its counting oracle
(`count-channelswitch.test.ts`; Apache-2.0, read 2026-10-23): Vexa scores a transcript against known turns, Tara scores the binder
against known turns, because the binder is where Tara's per-speaker decision is made. Nothing here talks to a browser, a model or
the network, and there is no wall clock: the same scenario always produces byte-identical frames.

Three headline metrics, deliberately close to Vexa's three:

* **completeness** — of the true turns, how many the binder named *at all* (a turn it never names is invisible to every rule that
  keys on a speaker, even though its audio was captured).
* **leakage** — of the frames it did name while someone was speaking, how many carried a name that is not the person actually
  talking. This is the hard gate: a truthful or merely lagging glow must never produce a wrong name.
* **attribution** — of the named speaking frames, how many are correct (precision), alongside the unknown rate that precision was
  bought with.

Plus two diagnostics a threshold change tends to move: `repaints` (a channel's name changing *within* one continuous turn) and
`dropouts` (a name withdrawn mid-turn), and `silent_named`, the decay tail where a channel keeps its last name after its speaker
stops. That tail is not a leak — the audio is silence and the engine's voice detection discards it — but it is measured so the cost
of a long window stays visible: a channel falls back to UNKNOWN only when its decayed score reaches the floor, which for a long turn
takes `tau` · ln(score / floor), roughly two time constants.

Ground truth is a property of the fixture, never of the binder's output: a scenario says who spoke, and a frame's truth is the turn
covering its timestamp. A frame where nobody is speaking has no truth, so a name there can never count as correct.
"""
from __future__ import annotations

from dataclasses import dataclass

from .binding import MAX_CHANNELS, BinderConfig, ChannelBinder

DEFAULT_FRAME_MS = 100          # one energy reading per channel per 100 ms, the cadence the page taps deliver
LOUD = 0.5                      # comfortably above the binder's loud threshold
QUIET = 0.001                   # below it: this frame adds no evidence


@dataclass(frozen=True)
class Turn:
    """One true utterance: this speaker was talking on this channel over [start_ms, end_ms)."""

    speaker: str
    channel: int
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if not self.speaker.strip():
            raise ValueError("a turn needs a speaker")
        if not 0 <= self.channel < MAX_CHANNELS:
            raise ValueError(f"channel {self.channel} is outside 0..{MAX_CHANNELS - 1}")
        if self.end_ms <= self.start_ms:
            raise ValueError(f"turn {self.speaker} on channel {self.channel} ends before it starts")

    def covers(self, ts_ms: float) -> bool:
        return self.start_ms <= ts_ms < self.end_ms


@dataclass(frozen=True)
class GlowEdge:
    """One speaking edge Meet's call screen showed: `end=False` is a tile lighting up, `True` is it going dark."""

    name: str
    ts_ms: int
    end: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a glow edge needs a name")
        if self.ts_ms < 0:
            raise ValueError("a glow edge cannot happen before the meeting starts")


@dataclass(frozen=True)
class Scenario:
    """A replayable fixture: the truth, the hint Meet showed, and how the bot's own name arrives.

    `self_name_at_ms` models the local tile's name marker rendering late: before it, the bot's own glow can still reach the
    binder (a real leak the binder purges when the name finally arrives); `None` means it was known from the start.
    """

    name: str
    turns: tuple[Turn, ...]
    glow: tuple[GlowEdge, ...]
    self_name: str | None = None
    self_name_at_ms: int | None = None
    frame_ms: int = DEFAULT_FRAME_MS
    duration_ms: int | None = None
    resets: tuple[int, ...] = ()
    channels: tuple[int, ...] = ()          # channels that exist but never carry a true turn (a muted participant)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a scenario needs a name")
        if self.frame_ms <= 0:
            raise ValueError("frame_ms must be positive")
        if self.self_name_at_ms is not None and not self.self_name:
            raise ValueError("a late self name needs a name to arrive")
        by_channel: dict[int, list[Turn]] = {}
        for turn in self.turns:
            by_channel.setdefault(turn.channel, []).append(turn)
        for channel, turns in by_channel.items():
            turns.sort(key=lambda t: t.start_ms)
            for earlier, later in zip(turns, turns[1:], strict=False):
                if later.start_ms < earlier.end_ms:              # two people on one transport channel is not a fixture, it is a bug
                    raise ValueError(f"channel {channel} has overlapping turns ({earlier.speaker}, {later.speaker})")
        if self.duration_ms is not None and self.duration_ms <= 0:
            raise ValueError("duration_ms must be positive")
        for reset in self.resets:
            if reset < 0:
                raise ValueError("a reset cannot happen before the meeting starts")
        for channel in self.channels:
            if not 0 <= channel < MAX_CHANNELS:
                raise ValueError(f"channel {channel} is outside 0..{MAX_CHANNELS - 1}")


@dataclass(frozen=True)
class Frame:
    """What the binder answered for one channel at one instant, next to who was really talking."""

    ts_ms: int
    channel: int
    truth: str | None
    answer: str | None


@dataclass(frozen=True)
class Score:
    """The ladder for one scenario. Counts first, so a percentage can never hide a small denominator."""

    scenario: str
    frames: int
    speaking_frames: int
    named_frames: int
    correct_frames: int
    wrong_frames: int
    unknown_frames: int
    silent_named: int
    turns: int
    named_turns: int
    correct_turns: int
    repaints: int
    dropouts: int
    wrong_names: tuple[tuple[str, str], ...]

    @property
    def completeness(self) -> float:
        """True turns the binder named at all (right or wrong). A turn it never names is never attributed."""
        return self.named_turns / self.turns if self.turns else 0.0

    @property
    def correct_turn_rate(self) -> float:
        """True turns that were named correctly at least once, i.e. the person became visible during their turn."""
        return self.correct_turns / self.turns if self.turns else 0.0

    @property
    def precision(self) -> float:
        """Of the named speaking frames, the share that named the person actually talking."""
        return self.correct_frames / self.named_frames if self.named_frames else 0.0

    @property
    def leakage(self) -> float:
        """Of the named speaking frames, the share that named somebody else. The hard gate."""
        return self.wrong_frames / self.named_frames if self.named_frames else 0.0

    @property
    def unknown_rate(self) -> float:
        """Of the frames where somebody was speaking, the share the binder left unnamed."""
        return self.unknown_frames / self.speaking_frames if self.speaking_frames else 0.0

    @property
    def line(self) -> str:
        """One grep-friendly line, the shape a report or a future CLI can print without re-deriving anything."""
        return (
            f"{self.scenario}: turns={self.turns} completeness={self.named_turns}/{self.turns} "
            f"correct_turns={self.correct_turns}/{self.turns} named_frames={self.named_frames}/{self.speaking_frames} "
            f"precision={self.precision:.3f} leakage={self.leakage:.3f} wrong={self.wrong_frames} "
            f"unknown={self.unknown_frames} silent_named={self.silent_named} repaints={self.repaints} "
            f"dropouts={self.dropouts}"
        )


def replay(scenario: Scenario, config: BinderConfig | None = None) -> tuple[Frame, ...]:
    """Drive the real binder over the scenario on a virtual clock. Deterministic: no wall clock, no I/O, no randomness.

    Glow edges at instant `t` are applied before the audio frames at `t`, which is what the page does (it polls the DOM, then a
    tapped channel delivers its frame). An offset is applied before an onset at the same instant, so a handover at `t` is
    `{new speaker}` rather than a one-frame overlap of both.
    """
    channels = sorted({turn.channel for turn in scenario.turns} | set(scenario.channels))
    glow = sorted(scenario.glow, key=lambda e: (e.ts_ms, 0 if e.end else 1))
    duration = scenario.duration_ms or max(
        [turn.end_ms for turn in scenario.turns] + [edge.ts_ms for edge in glow] + [0]
    ) + scenario.frame_ms
    binder = ChannelBinder(
        config,
        self_name=scenario.self_name if scenario.self_name_at_ms is None else None,
    )
    frames: list[Frame] = []
    edge = 0
    reset_at = sorted(scenario.resets)
    reset = 0
    for ts in range(0, duration, scenario.frame_ms):
        while reset < len(reset_at) and ts >= reset_at[reset]:
            binder.reset()                                   # a new page numbers its channels from one again
            reset += 1
        while edge < len(glow) and glow[edge].ts_ms <= ts:
            binder.record_glow(glow[edge].name, glow[edge].end, float(glow[edge].ts_ms))
            edge += 1
        if scenario.self_name_at_ms is not None and ts >= scenario.self_name_at_ms:
            if binder.self_name != scenario.self_name:
                binder.set_self_name(scenario.self_name)
        for channel in channels:
            turn = _covering(scenario.turns, channel, ts)
            truth = turn.speaker if turn is not None else None
            energy = LOUD if truth is not None else QUIET
            frames.append(Frame(ts, channel, truth, binder.name_for_channel(channel, float(ts), energy)))
    return tuple(frames)


def score(scenario: Scenario, frames: tuple[Frame, ...]) -> Score:
    """Join the replay against the scenario's truth and count every way a frame can be right, wrong or silent."""
    correct = wrong = unknown = named = speaking = silent_named = 0
    wrong_names: set[tuple[str, str]] = set()
    for frame in frames:
        if frame.truth is None:
            if frame.answer is not None:
                silent_named += 1                                # the decay tail, not a leak: the audio here is silence
            continue
        speaking += 1
        if frame.answer is None:
            unknown += 1
            continue
        named += 1
        if frame.answer == frame.truth:
            correct += 1
        else:
            wrong += 1
            wrong_names.add((frame.truth, frame.answer))
    named_turns = correct_turns = repaints = dropouts = 0
    for turn in scenario.turns:
        answers = [f.answer for f in frames if f.channel == turn.channel and turn.covers(f.ts_ms)]
        if any(a is not None for a in answers):
            named_turns += 1
        if any(a == turn.speaker for a in answers):
            correct_turns += 1
        previous: str | None = None
        last_named: str | None = None
        for answer in answers:
            if answer is not None:
                if last_named is not None and answer != last_named:
                    repaints += 1                                # the same person's turn changed name under the reader
                last_named = answer
            elif previous is not None:
                dropouts += 1                                    # a name was withdrawn mid-turn
            previous = answer
    return Score(
        scenario=scenario.name,
        frames=len(frames),
        speaking_frames=speaking,
        named_frames=named,
        correct_frames=correct,
        wrong_frames=wrong,
        unknown_frames=unknown,
        silent_named=silent_named,
        turns=len(scenario.turns),
        named_turns=named_turns,
        correct_turns=correct_turns,
        repaints=repaints,
        dropouts=dropouts,
        wrong_names=tuple(sorted(wrong_names)),
    )


def _covering(turns: tuple[Turn, ...], channel: int, ts_ms: int) -> Turn | None:
    for turn in turns:
        if turn.channel == channel and turn.covers(ts_ms):
            return turn
    return None
