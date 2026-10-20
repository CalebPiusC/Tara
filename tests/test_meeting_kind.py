"""Meeting-kind awareness: style only, inferred with hysteresis from validated signals.

These tests cover the tracker and its effect on the behaviour plan. They cannot prove how Tara
sounds; the kind layer never decides whether she may speak.
"""
from tara.behaviour.controller import BehaviourController
from tara.behaviour.meeting_kind import MeetingKind, MeetingKindTracker
from tara.behaviour.profiles import Profile
from tara.config import Mode

TECH_1 = "we must fix the api endpoint and the database migration"
TECH_2 = "the deploy pipeline and the cache latency are the issue"
CLIENT_1 = "the customer contract and pricing renewal are due"
CLIENT_2 = "the invoice and the proposal go to procurement"
CASUAL_1 = "haha lunch was fun, see you at the weekend"
CASUAL_2 = "coffee and a movie later"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def ctl(profile: Profile = Profile.CONVERSATIONAL, kind: str = "", mode: Mode = Mode.NATURAL) -> BehaviourController:
    return BehaviourController(profile, mode, kinds=MeetingKindTracker(kind, mode), clock=Clock())


def turn(c: BehaviourController, text: str):
    return c.observe(text, final=True)


def test_override_wins_and_interviews_are_always_interviews():
    assert MeetingKindTracker("casual", Mode.GROUP_INTERVIEW).kind is MeetingKind.CASUAL
    for mode in (Mode.INTERVIEW_STRUCTURED, Mode.GROUP_INTERVIEW):
        t = MeetingKindTracker("", mode)
        assert t.kind is MeetingKind.INTERVIEW
        t.observe(TECH_1)
        t.observe(TECH_2)
        assert t.kind is MeetingKind.INTERVIEW                   # conversation cannot override the mode
    t = MeetingKindTracker("technical", Mode.NATURAL)
    assert t.kind is MeetingKind.TECHNICAL
    t.set_override(None)
    assert t.kind is None                                        # explicit override is also removable


def test_incident_is_urgent_and_forces_a_calm_plan():
    t = MeetingKindTracker()
    t.observe("production is down, sev 1 incident", urgent=True)
    assert t.kind is MeetingKind.INCIDENT
    plan = turn(ctl(), "production is down, sev 1 incident, lol")
    assert plan.kind == "incident"
    assert plan.regime == "serious" and plan.humour == "none" and plan.laughter is False
    assert plan.tone == "serious" and plan.expressiveness == "subtle"
    for _ in range(3):
        t.observe("a normal update with no evidence of an incident")
    assert t.kind is None


def test_kind_needs_sustained_evidence_and_a_clear_margin():
    t = MeetingKindTracker()
    assert t.observe(TECH_1) is None                             # one turn is not enough
    assert t.kind is None
    assert t.observe(TECH_2) is MeetingKind.TECHNICAL
    mixed = MeetingKindTracker()
    assert mixed.observe("the api pricing") is None               # 1 technical vs 1 client: no clear lead
    assert mixed.observe("the api pricing") is None               # still tied after two turns
    assert mixed.kind is None


def test_switch_cooldown_stops_the_kind_flapping():
    t = MeetingKindTracker(window=4, margin=1.0, switch_after=2, cooldown_turns=3)
    assert t.observe(TECH_1) is None
    assert t.observe(TECH_2) is MeetingKind.TECHNICAL
    assert t.observe(CLIENT_1) is MeetingKind.TECHNICAL           # cooldown + lead not yet sustained
    assert t.observe(CLIENT_2) is MeetingKind.TECHNICAL
    assert t.observe(CLIENT_1) is MeetingKind.TECHNICAL
    assert t.observe(CLIENT_2) is MeetingKind.CLIENT


def test_casual_is_never_assumed_without_evidence():
    t = MeetingKindTracker()
    assert t.kind is None
    assert t.observe(CASUAL_1) is None
    assert t.observe(CASUAL_2) is MeetingKind.CASUAL
    plan = turn(ctl(), "the release is on track")
    assert plan.kind == ""                                        # no kind, no adjustment
    assert plan.humour == "none"


def test_brainstorm_relaxes_the_cue_but_never_invites_laughter_without_a_joke():
    relaxed = turn(ctl(kind="brainstorm"), "what if we explore this concept")
    assert relaxed.kind == "brainstorm" and relaxed.regime == "neutral"
    assert relaxed.humour == "light" and relaxed.laughter is False
    assert "brainstorm_light_humour" in relaxed.reasons
    joked = turn(ctl(kind="brainstorm"), "haha, what if we explore this concept")
    assert joked.humour == "light" and joked.laughter is True      # a participant joke is still required

    capped = turn(ctl(Profile.PROFESSIONAL, kind="brainstorm"), "what if we explore this concept")
    assert capped.humour == "none"                                 # the professional ceiling wins


def test_kind_can_only_lower_humour_never_raise_it_above_the_profile_cap():
    capped = turn(ctl(Profile.EXPRESSIVE, kind="technical"),
                  "haha that script name is hilarious, the api endpoint is fine")
    assert capped.humour == "restrained"                           # expressive would be "playful"
    casual = turn(ctl(Profile.PROFESSIONAL, kind="casual"), "haha nice one")
    assert casual.humour == "restrained"                           # never above the professional cap


def test_plan_carries_the_kind_and_guidance_includes_its_text():
    c = ctl(kind="technical")
    plan = turn(c, TECH_2)
    assert plan.kind == "technical"
    note = c.guidance()
    assert note is not None and "technical discussion" in note and "Internal note" in note
    quiet = ctl()
    turn(quiet, "the release is on track")
    assert quiet.guidance() is None                                # a calm default needs no note
