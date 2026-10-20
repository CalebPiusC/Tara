"""Behaviour layer: deterministic rules, quality checks, profiles, expressions, config.
These test the LOGIC. They cannot prove how the model's voice sounds (see tests/live)."""
import inspect
import time

import pytest
from helpers import POLICY, cfg, group_cfg

from tara.behaviour.checks import MAX_WORDS, flag_text, restates_question
from tara.behaviour.controller import BehaviourController, BehaviourPlan, render_guidance
from tara.behaviour.expressions import Expression, ExpressionEmitter, ExpressionEvent, ListExpressionSink, emotion_for
from tara.behaviour.persona import persona_instruction
from tara.behaviour.profiles import PROFILES, Profile, clamp, effective_profile, parse_profile
from tara.behaviour.signals import analyze
from tara.config import PROFILE_NAMES, Mode, validate
from tara.context import parse_draft
from tara.settings import RuntimeSettings
from tara.settings import validate as validate_settings
from tara.voice.instructions import live_system_instruction


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def ctl(profile=Profile.CONVERSATIONAL, mode=Mode.NATURAL, clock=None, **kw):
    return BehaviourController(profile, mode, clock=clock or Clock(), **kw)


def turn(c, text):
    return c.observe(text, final=True)


# ---- 1. a genuinely funny remark ---------------------------------------------------------------------------
def test_funny_remark_allows_a_light_reaction_in_a_relaxed_profile():
    c = ctl(Profile.CONVERSATIONAL)
    p = turn(c, "haha Tara, that deploy script name is hilarious, just kidding about the rollback")
    assert p.regime == "playful" and p.humour == "light" and p.laughter and p.tone == "playful"
    assert p.intent == "react_lightly"


def test_professional_profile_keeps_humour_restrained_and_never_initiates():
    c = ctl(Profile.PROFESSIONAL)
    p = turn(c, "lol nice one")
    assert p.humour == "restrained" and p.expressiveness == "subtle"
    quiet = turn(ctl(Profile.PROFESSIONAL), "can you summarise the status of the release")
    assert quiet.humour == "none" and not quiet.laughter                     # no joke without a playful room


def test_humour_needs_a_playful_room_in_every_profile():
    for prof in Profile:
        p = turn(ctl(prof), "what is the plan for the migration this week")
        assert p.humour == "none" and not p.laughter, prof


# ---- 2. disappointing news ------------------------------------------------------------------------------------
def test_disappointing_news_gets_acknowledgement_first_no_humour_and_gentle_tone():
    for prof in Profile:
        p = turn(ctl(prof), "unfortunately we didn't get the contract, and I'm pretty disappointed")
        assert p.intent == "empathize_then_help" and p.tone == "gentle" and p.humour == "none" and not p.laughter
        assert p.expressiveness in ("none", "subtle") and p.regime == "empathy"
    g = ctl().observe("ugh this keeps breaking and I'm so tired of it", final=True)
    assert g.intent == "empathize_then_help"


# ---- 3. correction ----------------------------------------------------------------------------------------------
def test_correction_is_recognised_so_tara_updates_instead_of_defending():
    p = turn(ctl(), "no, I meant the staging cluster, not production")
    assert p.intent == "accept_correction"
    assert analyze("that's not what I said, actually it's the second option").correction


# ---- 4. interruption --------------------------------------------------------------------------------------------
def test_after_being_interrupted_replies_get_shorter_then_recover():
    c = ctl(Profile.CONVERSATIONAL)
    base = turn(c, "Tara, walk me through the architecture").verbosity
    assert base == "detailed"                                            # an explicit ask for detail still wins
    c.note_interrupted()
    p = c.observe("so what should we do next about the schema", final=False)
    assert p.verbosity == "brief" and "recently_interrupted" in p.reasons
    for t in ("ok", "right", "sure thing"):
        turn(c, t)
    assert c.observe("what should we do about the cache", final=False).verbosity != "brief" or "recently_interrupted" not in c.last_plan.reasons


# ---- 5. direct technical question ------------------------------------------------------------------------------------
def test_direct_questions_are_answered_and_detail_is_proportionate():
    c = ctl(Profile.PROFESSIONAL)
    short = c.observe("Tara, is the cache enabled?", final=True)
    assert short.intent == "answer" and short.verbosity == "brief"
    deep = c.observe("Tara, explain how the retry logic avoids duplicate payments", final=True)
    assert deep.intent == "answer" and deep.verbosity == "detailed"
    assert c.observe("Tara, quickly, what port does it use", final=True).verbosity == "brief"


# ---- 6/9. serious moments --------------------------------------------------------------------------------------------------
def test_serious_incident_restrains_everything_even_if_someone_jokes():
    c = ctl(Profile.EXPRESSIVE)
    p = turn(c, "production is down and customers are affected, lol this is fine")
    assert p.regime == "serious" and p.humour == "none" and not p.laughter
    assert p.expressiveness == "subtle" and p.verbosity == "brief" and p.tone == "serious"
    for t in ("ok rolling back", "lol", "haha"):                             # the mood does not flip instantly
        assert turn(c, t).humour == "none"
    for _ in range(6):
        turn(c, "checking the dashboards now")
    assert turn(c, "haha ok that was stressful, funny how it works out").humour != "none"   # only after it has passed


def test_sensitive_topics_hold_longer_and_conflict_blocks_humour():
    c = ctl(Profile.CONVERSATIONAL)
    p = turn(c, "my father passed away last week so I may be slow this week")
    assert p.regime == "sensitive" and p.humour == "none" and p.intent == "empathize_then_help"
    for _ in range(7):
        p = turn(c, "lol ok")
    assert p.humour == "none"                                                # still held after several turns
    assert turn(ctl(), "that's ridiculous, you always do this").regime == "serious"


def test_interviews_are_always_professional_with_no_humour():
    c = ctl(Profile.EXPRESSIVE, Mode.GROUP_INTERVIEW)
    p = turn(c, "haha that's funny, lol")
    assert p.humour == "none" and not p.laughter and p.formality == "formal"
    assert effective_profile(Profile.EXPRESSIVE, group_cfg()) is Profile.PROFESSIONAL
    assert effective_profile(Profile.EXPRESSIVE, cfg()) is Profile.EXPRESSIVE


# ---- 10. informal meeting -------------------------------------------------------------------------------------------------------
def test_informal_meeting_permits_light_humour_and_casual_register():
    c = ctl(Profile.CONVERSATIONAL)
    p = turn(c, "hey Tara, gonna be a quick one, lol, yeah this part is kinda funny")
    assert p.formality == "casual" and p.humour == "light" and p.laughter
    e = turn(ctl(Profile.EXPRESSIVE), "haha good one")
    assert e.humour == "playful" and e.expressiveness == "animated"


# ---- 11. quiet / yielding ----------------------------------------------------------------------------------------------------------
def test_yielding_and_mute_remove_humour_and_set_stay_back_intent():
    c = ctl()
    turn(c, "haha great")
    c.note_yield(True)
    p = turn(c, "Tara what do you think")
    assert p.intent == "stay_back" and p.humour == "none"
    c.note_yield(False); c.note_muted(True)
    assert turn(c, "haha another joke").humour == "none"


# ---- 12. repeated context does not produce canned reactions ---------------------------------------------------------------------------
def test_same_joke_context_repeated_does_not_machine_gun_laughter():
    clock = Clock()
    c = ctl(Profile.CONVERSATIONAL, clock=clock)
    reactions = 0
    for _ in range(20):
        p = turn(c, "haha that's funny")
        if p.laughter:
            reactions += 1
            c.note_response_started()                                        # Tara actually used the allowance
        clock.t += 5
    assert reactions == 2                                                    # first at once, the next only after the cooldown (5 turns AND 60 s)
    assert ctl(Profile.PROFESSIONAL, clock=Clock()).params.laughter_cooldown_s > PROFILES[Profile.EXPRESSIVE].laughter_cooldown_s


def test_unused_laughter_allowance_is_not_spent_when_tara_stays_silent():
    c = ctl(Profile.CONVERSATIONAL)
    assert turn(c, "haha funny").laughter
    assert c.observe("haha funny again", final=False).laughter              # Tara never spoke, so nothing was consumed


def test_guidance_is_sent_once_per_situation_and_rate_limited_except_when_heavy():
    clock = Clock()
    c = ctl(Profile.CONVERSATIONAL, clock=clock, guidance_min_interval_s=15)
    assert c.guidance() is None                                              # nothing observed yet
    turn(c, "haha nice")
    first = c.guidance()
    assert first and "playful" in first and "Internal note" in first
    assert c.guidance() is None                                              # unchanged situation: silent
    clock.t += 1
    turn(c, "unfortunately we lost the customer and the incident is urgent")
    heavy = c.guidance()
    assert heavy and ("serious" in heavy or "frustrated" in heavy or "sensitive" in heavy)   # bypasses the 15 s limit
    clock.t += 1
    for _ in range(7):
        turn(c, "ok")
    assert c.guidance() is None                                              # calming down is rate limited
    clock.t += 20
    assert "steady" in (c.guidance() or "")


def test_guidance_never_repeats_participant_words():
    c = ctl()
    turn(c, "unfortunately project nightingale was cancelled by Acme Corp")
    note = c.guidance() or ""
    assert note and "nightingale" not in note.lower() and "acme" not in note.lower()


def test_repeated_openers_feed_back_as_variation_guidance():
    c = ctl()
    turn(c, "Tara what is the status")
    for _ in range(3):
        c.note_response("Sure thing, here is where we are on that.")
    turn(c, "and the next one")
    g = c.guidance()
    assert g and "vary how you open" in g and "sure thing here" in g
    assert c.flag_counts.get("repeated_opener", 0) >= 2


def test_overlong_replies_create_verbosity_pressure():
    c = ctl()
    turn(c, "Tara what is the status of the migration plan")
    long = " ".join(["word"] * (MAX_WORDS["concise"] + 20))
    flags = c.note_response(long)
    assert any(f.name == "overlong" for f in flags) and c.pressure == 1
    assert c.observe("and what about the rollout order for tomorrow", final=False).verbosity == "brief"


# ---- quality checks ------------------------------------------------------------------------------------------------------------------------------
def names(text, **kw):
    return {f.name for f in flag_text(text, **kw)}


def test_checks_flag_stock_phrases_ai_disclaimers_and_human_claims():
    assert "canned_opener" in names("That's a great question! Let me explain.")
    assert "canned_opener" in names("Absolutely! I can do that.")
    assert "canned_opener" in names("I completely understand your frustration.")
    assert "canned_opener" in names("I would be happy to assist you with that.")
    assert "ai_disclaimer" in names("As an AI language model I can't feel that.")
    assert "human_experience_claim" in names("Oh I remember when I was a kid we did the same thing.")
    assert "human_experience_claim" in names("My weekend was great, thanks for asking.")
    assert "canned_opener" not in names("Absolutely not, that would break the cache.")      # legitimate use
    assert names("I'm an AI participant here, so I can't say I've lived that, but the data points the same way.") == set()


def test_checks_flag_forced_laughter_lists_stage_directions_and_restating():
    assert "unprompted_laughter" in names("Haha ha ha, yes.")
    assert "unprompted_laughter" not in names("Haha, yes.", laughter_allowed=True)
    assert "exaggerated_laughter" in names("ha ha ha ha ha ha that's it", laughter_allowed=True)
    assert "spoken_list" in names("Firstly we scale. Secondly we cache. Thirdly we monitor.")
    assert "stage_direction" in names("[laughs] sure, go on")
    assert "stage_direction" in names("*sighs* fine")
    q = "how would you design the retry logic for payment webhooks"
    assert restates_question("I'd start with idempotency keys and a dedupe table, then add backoff", q) is False
    assert "restates_question" in names("How would you design the retry logic for payment webhooks, good, I'd start with idempotency", question=q)
    assert "repeated_opener" in names("Sure thing, here we go.", recent_openers=("sure thing here",))


def test_natural_responses_pass_and_verbosity_limits_scale():
    ok = ["Yeah, the cache falls back to the database, which is slower but correct.",
          "Not sure yet. I'd want to see the load numbers before saying.",
          "I see the concern, but I think scaling the writers is the bigger risk.",
          "Wait, that changes things a little."]
    for t in ok:
        assert names(t) == set(), t
    assert "overlong" in names(" ".join(["w"] * 40), verbosity="brief") and "overlong" not in names(" ".join(["w"] * 40))
    assert names("") == set()


# ---- profiles, config, settings -------------------------------------------------------------------------------------------------------------------
def test_profiles_adjust_limits_without_changing_the_character():
    p, c, e = (PROFILES[x] for x in Profile)
    assert (p.max_humour, c.max_humour, e.max_humour) == ("restrained", "light", "playful")
    assert p.laughter_cooldown_s > c.laughter_cooldown_s > e.laughter_cooldown_s
    assert clamp("animated", ("none", "subtle", "moderate", "animated"), "subtle") == "subtle"
    assert parse_profile(None) is Profile.PROFESSIONAL and parse_profile(" Expressive ") is Profile.EXPRESSIVE
    with pytest.raises(ValueError):
        parse_profile("sarcastic")
    assert PROFILE_NAMES == {p.value for p in Profile}


def test_meeting_config_and_organizer_draft_profile_validation():
    assert validate(cfg(behaviour_profile="conversational"), POLICY).ok
    bad = validate(cfg(behaviour_profile="sarcastic"), POLICY)
    assert not bad.ok and bad.errors()[0].field == "behaviour_profile"
    warn = validate(group_cfg(behaviour_profile="expressive"), POLICY)
    assert warn.ok and any(i.field == "behaviour_profile" and i.severity == "warning" for i in warn.issues)
    d = {"purpose": "p", "mode": "natural", "behaviour_profile": "expressive"}
    assert parse_draft(d)[0].behaviour_profile == "expressive"
    assert parse_draft({"purpose": "p", "mode": "natural"})[0].behaviour_profile == ""


def test_persona_settings_env_and_validation():
    s = RuntimeSettings.from_env({"TARA_PERSONA_PROFILE": "Conversational", "TARA_PERSONA_VOCAL_REACTIONS": "1",
                                  "TARA_PERSONA_GUIDANCE": "0", "TARA_PERSONA_FLOOR_WAIT_MS": "900"})
    assert s.persona.profile == "conversational" and s.persona.vocal_reactions and not s.persona.guidance_injection
    assert s.persona.floor_wait_ms == 900
    assert RuntimeSettings().persona.profile == "professional"               # the default for meetings
    errs = {i.field for i in validate_settings(RuntimeSettings.from_env({"TARA_PERSONA_PROFILE": "goofy",
                                                                         "TARA_PERSONA_FLOOR_WAIT_MS": "-1",
                                                                         "TARA_LIVE_TOOL_BEHAVIOR": "sometimes"})) if i.severity == "error"}
    assert errs == {"TARA_PERSONA_PROFILE", "TARA_PERSONA_*", "TARA_LIVE_TOOL_BEHAVIOR"}


def test_persona_instruction_describes_style_rules_without_scripted_lines_or_permissions():
    for prof in Profile:
        text = persona_instruction(prof)
        for must in ("Disagree respectfully", "Do not claim human experiences", "never override the meeting's rules",
                     "contractions", "stage directions", "when interrupted stop"):
            assert must in text or must.lower() in text.lower(), (prof, must)
        assert "agree with everyone" in text and "do not agree with everyone" in text.lower()
        assert "permission" not in text.lower() or "never override" in text
    assert "never write stage directions" in persona_instruction(Profile.PROFESSIONAL)
    assert "react briefly in your own voice" in persona_instruction(Profile.CONVERSATIONAL, vocal_reactions=True)
    assert "react briefly in your own voice" not in persona_instruction(Profile.CONVERSATIONAL, vocal_reactions=False)
    assert "Restrained" in persona_instruction(Profile.PROFESSIONAL) or "restrained" in persona_instruction(Profile.PROFESSIONAL)
    plain = live_system_instruction(cfg())
    with_persona = live_system_instruction(cfg(), profile=Profile.EXPRESSIVE)
    assert "Personality:" not in plain and "Personality:" in with_persona and "animated" in with_persona.lower()
    assert "Always speak English" in plain and "Always speak English" in with_persona


# ---- expressions ------------------------------------------------------------------------------------------------------------------------------------------
def test_expression_schema_validation():
    ok = ExpressionEvent(Expression.SPEAKING_STARTED, 5, "s1", 1)
    ok.validate()
    ExpressionEvent(Expression.AMUSED, 5, "s1", 1, "moderate", "context").validate()
    for bad in (ExpressionEvent(Expression.AMUSED, 5, "s1", 1, "moderate", "runtime"),        # emotion needs context basis
                ExpressionEvent(Expression.LISTENING, 5, "s1", None, "subtle", "context"),
                ExpressionEvent(Expression.LISTENING, 5, "s1", None, "huge", "runtime"),
                ExpressionEvent(Expression.LISTENING, -1, "s1"),
                ExpressionEvent(Expression.LISTENING, 5, ""),
                ExpressionEvent(Expression.LISTENING, 5, "s1", -2)):
        with pytest.raises(ValueError):
            bad.validate()
    assert {e.value for e in Expression} == {"speaking_started", "speaking_stopped", "listening", "thinking", "amused",
                                             "surprised", "concerned", "interrupted", "yielding_control"}


def test_emitter_dedupes_state_events_rate_limits_emotions_and_isolates_sink_failures():
    clock = Clock()
    sink = ListExpressionSink()
    em = ExpressionEmitter(sink, "s1", clock=clock, emotion_min_interval_s=5)
    assert em.emit(Expression.LISTENING) and em.emit(Expression.LISTENING) is None
    assert em.emit(Expression.SPEAKING_STARTED, response_id=1) and em.emit(Expression.LISTENING)
    assert em.emit(Expression.AMUSED, response_id=1, intensity="subtle")
    assert em.emit(Expression.CONCERNED, response_id=1) is None                  # too soon after the last emotion
    clock.t += 6
    assert em.emit(Expression.CONCERNED, response_id=2)
    assert [e.kind for e in sink.events] == [Expression.LISTENING, Expression.SPEAKING_STARTED, Expression.LISTENING,
                                             Expression.AMUSED, Expression.CONCERNED]
    assert em.emit(Expression.LISTENING, intensity="enormous") is None and em.rejected == 1 and em.suppressed == 2

    class Boom:
        def emit(self, ev):
            raise RuntimeError("avatar crashed")
    safe = ExpressionEmitter(Boom(), "s1", clock=clock)
    assert safe.emit(Expression.LISTENING) is not None and safe.sink_errors == 1


def test_emotion_only_when_the_context_supports_it():
    assert emotion_for("playful", "light", True, "moderate", False, False) is Expression.AMUSED
    assert emotion_for("playful", "none", True, "moderate", False, False) is None          # laughter without humour allowed
    assert emotion_for("empathy", "none", False, "subtle", False, True) is Expression.CONCERNED
    assert emotion_for("neutral", "none", False, "moderate", True, False) is Expression.SURPRISED
    assert emotion_for("neutral", "none", False, "moderate", False, False) is None         # no cue, no emotion
    assert emotion_for("serious", "none", False, "subtle", True, False) is Expression.CONCERNED   # never surprised in a serious moment
    assert emotion_for("playful", "light", True, "none", False, False) is None


# ---- signals, latency and independence from permissions ---------------------------------------------------------------------------------------------------
def test_signals_basics_and_false_positive_resistance():
    s = analyze("Tara, can you walk me through the deploy?")
    assert s.is_question and s.addressed_by_name and s.asks_detail and not s.heavy and not s.playful
    assert not analyze("the server is down for maintenance window next week").playful
    assert analyze("the build is down to two failing tests").urgency       # known false positive; it only steers style, never permission
    assert analyze("Dear team, kindly note").formality == "formal" and analyze("yeah gonna do it").formality == "casual"
    assert analyze("").words == 0


def test_behaviour_is_synchronous_cheap_and_has_no_model_dependency():
    c = ctl()
    assert not inspect.iscoroutinefunction(c.observe) and not inspect.iscoroutinefunction(c.guidance)
    text = "Tara, unfortunately the migration failed again and I'm frustrated, haha what a day, can you explain how the rollback works?"
    t0 = time.perf_counter()
    for _ in range(300):
        c.observe(text, final=True)
        c.guidance()
    per_call_ms = (time.perf_counter() - t0) / 300 * 1000
    assert per_call_ms < 5, per_call_ms                                    # measured bound, generous; typically far lower
    src = inspect.getsource(BehaviourController)
    assert "await" not in src and "sleep" not in src                       # no delay is ever introduced


def test_plan_and_controller_cannot_grant_or_withdraw_permission():
    fields = set(BehaviourPlan.__dataclass_fields__)
    assert not any(w in f for f in fields for w in ("allow", "permit", "may_speak", "speak", "deny", "grant"))
    attrs = " ".join(dir(BehaviourController(Profile.EXPRESSIVE, Mode.NATURAL)))
    assert "gate" not in attrs and "participation" not in attrs and "policy" not in attrs
    note = render_guidance(ctl().observe("haha", final=True))
    assert "permission" not in note.lower() and "allowed to speak" not in note.lower()
