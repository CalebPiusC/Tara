import pytest

from tara.floor.runtime import TRANSITIONS, IllegalTransition, MeetingRuntime, Proposal, State
from tests.helpers import ready_rt


def prop(rt, now=1000, eid="t1", epoch=None):
    return Proposal("p1", eid, rt.epoch if epoch is None else epoch, now, "hello")


def test_happy_path_to_speaking():
    rt = ready_rt()
    for s in (State.GENERATING, State.VALIDATING, State.WAITING_TO_SPEAK, State.SPEAKING, State.LISTENING):
        rt.transition(s)
    assert rt.state is State.LISTENING


@pytest.mark.parametrize("src,dst", [
    (State.LISTENING, State.SPEAKING), (State.INITIALIZING, State.LISTENING),
    (State.STOPPED, State.LISTENING), (State.EVALUATING_TURN, State.SPEAKING)])
def test_illegal_transitions(src, dst):
    rt = MeetingRuntime(1000)
    rt.state = src
    with pytest.raises(IllegalTransition):
        rt.transition(dst)


def test_stopped_is_terminal():
    assert TRANSITIONS[State.STOPPED] == frozenset()


def test_speaking_only_reachable_via_waiting():
    for s, outs in TRANSITIONS.items():
        if State.SPEAKING in outs:
            assert s is State.WAITING_TO_SPEAK


def test_approve_ok():
    rt = ready_rt()
    assert rt.approve(prop(rt), 1500).approved


def test_stale_epoch_after_barge_in():
    rt = ready_rt(State.EVALUATING_TURN)
    p = prop(rt)
    rt.state = State.SPEAKING
    rt.on_barge_in()
    assert rt.state is State.INTERRUPTED
    assert rt.approve(p, 1100).reason == "stale_epoch"


def test_stale_age():
    rt = ready_rt(max_age=5000)
    assert rt.approve(prop(rt, now=0), 5001).reason == "stale_age"
    assert rt.approve(prop(rt, now=0), 5000).approved


def test_no_approval_during_takeover():
    rt = ready_rt()
    p = prop(rt)
    rt.start_takeover()
    assert rt.approve(prop(rt), 1100).reason == "takeover"
    assert not rt.approve(p, 1100).approved


def test_takeover_yields_when_speaking():
    rt = ready_rt()
    for s in (State.GENERATING, State.VALIDATING, State.WAITING_TO_SPEAK, State.SPEAKING):
        rt.transition(s)
    rt.start_takeover()
    assert rt.state is State.YIELDING_CONTROL
    rt.release_takeover()
    assert rt.state is State.LISTENING and not rt.takeover_active


def test_one_response_per_trigger():
    rt = ready_rt()
    assert rt.approve(prop(rt), 1100).approved
    rt.mark_answered("t1")
    assert rt.approve(prop(rt), 1100).reason == "duplicate_trigger"


def test_floor_busy_blocks():
    rt = ready_rt()
    rt.others_speaking.add("a")
    assert rt.approve(prop(rt), 1100).reason == "floor_busy"


def test_policy_silent_blocks():
    rt = ready_rt(policy_silent=True)
    assert rt.approve(prop(rt), 1100).reason == "policy_silent"


def test_reconnect_bumps_epoch_and_invalidates():
    rt = ready_rt()
    p = prop(rt)
    rt.on_reconnect()
    assert rt.state is State.RECOVERING
    rt.transition(State.LISTENING)
    assert rt.approve(p, 1100).reason == "stale_epoch"


def test_duplicate_events_and_bounded_memory():
    rt = MeetingRuntime(1000, dedupe_window=3)
    assert rt.accept_event("a") and not rt.accept_event("a")
    for e in "bcd":
        rt.accept_event(e)
    assert len(rt._seen) == 3
    assert rt.accept_event("a")  # evicted, accepted again: window is bounded by design


def test_invalid_age():
    with pytest.raises(ValueError):
        MeetingRuntime(0)


def test_idle_takeover_sets_flag_without_illegal_transition():
    rt = ready_rt(State.LISTENING)
    rt.start_takeover()
    assert rt.takeover_active and rt.state is State.LISTENING
