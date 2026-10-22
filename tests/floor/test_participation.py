from tara.core.config import Mode, SpeakingPolicy
from tara.floor.participation import Action, Intent, decide
from tara.floor.participation import IntentKind as K
from tara.floor.runtime import State
from tests.helpers import ROLES, TH, cfg, group_cfg, ready_rt, utt


def d(intent, u=None, c=None, rt=None):
    return decide(u or utt(), intent, rt or ready_rt(), c or cfg(), ROLES, TH)


def test_direct_address_allowed():
    assert d(Intent(K.ADDRESSED)).action is Action.ALLOW


def test_talking_about_tara_denied():
    assert d(Intent(K.ABOUT_TARA)).reason == "not_addressed"


def test_humans_debating_denied_even_if_useful():
    assert d(Intent(K.HUMAN_TO_HUMAN, usefulness=0.99, confidence=1)).action is Action.DENY


def test_proactive_only_when_policy_and_pause():
    c = cfg(policy=SpeakingPolicy.PROACTIVE_ALLOWED)
    i = Intent(K.HUMAN_TO_HUMAN, usefulness=0.9)
    assert d(i, utt(pause=2000), c).action is Action.ALLOW
    assert d(i, utt(pause=100), c).action is Action.DENY
    assert d(i, utt(pause=2000), cfg()).action is Action.DENY  # on_address_only


def test_proactive_blocked_while_others_speak():
    c = cfg(policy=SpeakingPolicy.PROACTIVE_ALLOWED)
    rt = ready_rt(); rt.others_speaking.add("a")
    assert d(Intent(K.UNCLEAR, usefulness=0.9), utt(pause=3000), c, rt).action is Action.DENY


def test_silent_policy_never_speaks():
    c = cfg(mode=Mode.OBSERVE, policy=SpeakingPolicy.SILENT)
    assert d(Intent(K.ADDRESSED), c=c).reason == "policy_silent"


def test_low_intent_confidence_denied():
    assert d(Intent(K.ADDRESSED, confidence=0.2)).reason == "low_intent_confidence"


def test_takeover_by_recruiter_with_confidence():
    assert d(Intent(K.TAKEOVER), utt("r", 0.99), group_cfg()).action is Action.TAKEOVER


def test_takeover_by_candidate_rejected():
    assert d(Intent(K.TAKEOVER), utt("a", 0.99), group_cfg()).reason == "takeover_not_authorized"


def test_takeover_by_unknown_or_low_confidence_rejected():
    assert d(Intent(K.TAKEOVER), utt(None, 0.2), group_cfg()).action is Action.DENY
    assert d(Intent(K.TAKEOVER), utt("r", 0.5), group_cfg()).action is Action.DENY


def test_takeover_disabled_in_config():
    c = cfg(takeover_phrases_enabled=False)
    assert d(Intent(K.TAKEOVER), utt("r"), c).action is Action.DENY


def test_after_takeover_no_speech():
    rt = ready_rt(); rt.start_takeover()
    assert d(Intent(K.ADDRESSED), rt=rt).reason == "takeover"


def test_interview_low_identity_asks_instead_of_allowing():
    r = d(Intent(K.ADDRESSED), utt("a", 0.4), group_cfg())
    assert r.action is Action.ASK_CLARIFICATION


def test_interview_unknown_speaker_never_allowed():
    assert d(Intent(K.ADDRESSED), utt(None, 0.0), group_cfg()).action is Action.ASK_CLARIFICATION


def test_interview_overlap_is_not_reliable():
    assert d(Intent(K.ADDRESSED), utt("a", 0.99, overlap=True), group_cfg()).action is Action.ASK_CLARIFICATION


def test_interview_reliable_identity_allows():
    assert d(Intent(K.ADDRESSED), utt("a", 0.97), group_cfg()).action is Action.ALLOW


def test_out_of_scope_refused():
    assert d(Intent(K.ADDRESSED, in_scope=False)).action is Action.REFUSE


def test_floor_busy_waits_unless_direct_question():
    rt = ready_rt(); rt.others_speaking.add("a")
    assert d(Intent(K.ADDRESSED), rt=rt).action is Action.WAIT
    rt2 = ready_rt(); rt2.others_speaking.add("a")
    assert d(Intent(K.ADDRESSED, is_direct_question=True), rt=rt2).action is Action.ALLOW


def test_already_speaking_denied():
    rt = ready_rt()
    for s in (State.GENERATING, State.VALIDATING, State.WAITING_TO_SPEAK, State.SPEAKING):
        rt.transition(s)
    assert d(Intent(K.ADDRESSED), rt=rt).reason == "already_speaking"


def test_not_ready_states():
    rt = ready_rt(State.LISTENING)
    rt.state = State.RECOVERING
    assert d(Intent(K.ADDRESSED), rt=rt).reason == "not_ready"


def test_silence_request_mutes():
    assert d(Intent(K.SILENCE_REQUEST)).action is Action.MUTE
