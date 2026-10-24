"""The energy/glow channel binder, without a browser: the rules that decide whether a name is attached to a channel at all.

Every case here is about refusing to answer as much as about answering: a channel gets a name only when the evidence is strong,
unambiguous and uniquely its own. These are the golden cases behind docs/google-meet/SPEAKER_BINDING.md.
"""
import math

import pytest

from tara.meet.binding import BinderConfig, ChannelBinder, pick_bound_name

LOUD = 0.5          # comfortably above loud_threshold
QUIET = 0.001       # below it: this frame adds no evidence


def frames(binder: ChannelBinder, channel: int, count: int, *, start: int = 0, step: int = 100, energy: float = LOUD) -> list[str | None]:
    """Feed `count` frames 100 ms apart and return what the binder answered for each one."""
    return [binder.name_for_channel(channel, start + i * step, energy) for i in range(count)]


def test_exactly_one_lit_tile_is_the_only_name_a_chunk_may_carry():
    assert pick_bound_name([]) is None                       # silence or settling
    assert pick_bound_name(["Alex"]) == "Alex"               # the live speaker
    assert pick_bound_name(["Alex", "Sam"]) is None          # overlap is ambiguous at the source: never guess


def test_a_name_is_attached_only_after_enough_agreeing_evidence():
    b = ChannelBinder()
    b.record_glow("Alex", False, 0)
    answers = frames(b, 1, 3)
    assert answers[0] is None and answers[1] is None         # one or two frames are not evidence enough
    assert answers[2] == "Alex"                              # the third clears the floor
    assert b.scores(1, 200)["Alex"] >= BinderConfig().min_score


def test_a_quiet_channel_never_earns_a_name_even_while_someone_is_lit():
    b = ChannelBinder()
    b.record_glow("Alex", False, 0)
    assert frames(b, 2, 30, energy=QUIET) == [None] * 30
    assert b.scores(2, 3000) == {}                           # nothing was ever recorded: a quiet channel is not evidence


def test_two_people_lit_together_leave_the_channel_unknown():
    b = ChannelBinder()
    b.record_glow("Alex", False, 0)
    b.record_glow("Sam", False, 0)
    assert frames(b, 3, 10) == [None] * 10                   # both fit equally: the margin refuses to choose
    assert b.scores(3, 900)["Alex"] == pytest.approx(b.scores(3, 900)["Sam"])


def test_the_speaker_with_more_solo_moments_wins_the_channel():
    b = ChannelBinder()
    b.record_glow("Alex", False, 0)
    b.record_glow("Sam", False, 0)
    frames(b, 4, 3)                                          # three frames of overlap: both accrue
    b.record_glow("Sam", True, 300)                          # Sam stops; Alex keeps talking
    answers = frames(b, 4, 5, start=300)
    assert answers[-1] == "Alex"
    assert b.scores(4, 700)["Alex"] > b.scores(4, 700)["Sam"] * BinderConfig().margin


def test_a_tile_is_claimed_by_one_channel_only_and_a_tie_claims_nobody():
    b = ChannelBinder()
    b.record_glow("Alex", False, 0)
    for i in range(5):
        b.name_for_channel(5, i * 100, LOUD)
        b.name_for_channel(6, i * 100, LOUD)
    assert b.scores(5, 400)["Alex"] == pytest.approx(b.scores(6, 400)["Alex"])
    # Two channels fit Alex exactly as well: neither may claim him (a tie is not a win).
    assert b.name_for_channel(5, 400, QUIET) is None
    assert b.name_for_channel(6, 400, QUIET) is None
    for i in range(5, 9):                                    # channel 6 goes quiet, so channel 5 leads
        b.name_for_channel(6, i * 100, QUIET)
        answer = b.name_for_channel(5, i * 100, LOUD)
    assert answer == "Alex" and b.name_for_channel(6, 800, QUIET) is None


def test_the_bots_own_tile_is_never_bound_to_a_remote_channel():
    b = ChannelBinder(self_name="Tara")
    b.record_glow("Tara", False, 0)                          # the bot's own glow is dropped on the way in
    assert b.glowing == ()
    assert frames(b, 7, 10) == [None] * 10


def test_learning_the_bots_name_late_collapses_a_leak_that_already_happened():
    b = ChannelBinder()
    b.record_glow("Tara", False, 0)                          # the self marker has not rendered yet: the glow leaks in
    assert frames(b, 8, 5)[-1] == "Tara"
    b.set_self_name("Tara")                                  # the marker appears: purge what leaked
    assert b.name_for_channel(8, 500, LOUD) is None
    assert b.scores(8, 500) == {}
    assert frames(b, 8, 5, start=600) == [None] * 5


def test_evidence_decays_so_a_channel_that_stops_being_that_person_becomes_unknown():
    b = ChannelBinder()
    b.record_glow("Alex", False, 0)
    assert frames(b, 9, 4)[-1] == "Alex"
    b.record_glow("Alex", True, 300)
    assert b.name_for_channel(9, 20_000, QUIET) is None      # 20 s later the old agreement has faded out
    assert b.scores(9, 20_000)["Alex"] < 0.01


def test_a_clock_that_steps_backwards_never_amplifies_old_evidence():
    b = ChannelBinder()
    b.record_glow("Alex", False, 10_000)
    b.name_for_channel(10, 10_000, LOUD)
    before = b.scores(10, 10_000)["Alex"]
    b.name_for_channel(10, 9_000, QUIET)                     # a backwards timestamp must not inflate the score
    assert b.scores(10, 9_000)["Alex"] == pytest.approx(before)


def test_impossible_inputs_are_refused_rather_than_believed():
    b = ChannelBinder()
    b.record_glow("Alex", False, 0)
    assert b.name_for_channel(-1, 0, LOUD) is None
    assert b.name_for_channel(64, 0, LOUD) is None
    assert b.name_for_channel(1, 0, float("nan")) is None
    assert b.name_for_channel(1, 0, float("inf")) is None
    b.record_glow("   ", False, 0)                           # a blank name is not a participant
    assert b.glowing == ("Alex",)
    with pytest.raises(ValueError):
        ChannelBinder(BinderConfig(margin=0.5))
    with pytest.raises(ValueError):
        ChannelBinder(BinderConfig(tau_ms=0))


def test_a_reconnect_forgets_channels_but_not_who_the_bot_is():
    b = ChannelBinder(self_name="Tara")
    b.record_glow("Alex", False, 0)
    assert frames(b, 11, 4)[-1] == "Alex"
    b.reset()                                                # a new page numbers its channels from one again
    assert b.name_for_channel(11, 400, LOUD) is None and b.glowing == ()
    assert b.self_name == "Tara"


def test_the_glow_edges_are_a_set_not_a_counter():
    b = ChannelBinder()
    b.record_glow("Alex", False, 0)
    b.record_glow("Alex", False, 10)                         # a repeated onset is not a second speaker
    assert b.glowing == ("Alex",)
    b.record_glow("Alex", True, 20)
    b.record_glow("Alex", True, 30)                          # nor is a repeated offset
    assert b.glowing == ()
    b.record_glow("Sam", False, 40)
    assert b.glowing == ("Sam",) and math.isclose(b.scores(99, 0).get("Sam", 0.0), 0.0)
