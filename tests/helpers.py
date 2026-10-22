from tara.core.config import AppPolicy, MeetingConfig, Mode, ParticipantSpec, Role, SpeakingPolicy, ToolSpec
from tara.floor.participation import Attribution, Thresholds, Utterance
from tara.floor.runtime import MeetingRuntime, State

DISCLOSURE = "I am Tara, an AI assistant. This meeting is transcribed."
TH = Thresholds(min_intent_confidence=0.5, proactive_usefulness=0.7,
                proactive_min_pause_ms=1500, default_attribution=0.8)


def cfg(**kw):
    base = dict(purpose="Release planning", mode=Mode.NATURAL, duration_min=30,
                policy=SpeakingPolicy.ON_ADDRESS_ONLY, disclosure_text=DISCLOSURE)
    base.update(kw)
    return MeetingConfig(**base)


def group_cfg(**kw):
    ps = (ParticipantSpec("a", "Candidate A", Role.CANDIDATE),
          ParticipantSpec("b", "Candidate B", Role.CANDIDATE),
          ParticipantSpec("r", "Recruiter", Role.RECRUITER))
    return cfg(mode=Mode.GROUP_INTERVIEW, policy=SpeakingPolicy.MODERATED, participants=ps,
               individual_reports=True, attribution_min_confidence=0.9, **kw)


def ready_rt(state=State.EVALUATING_TURN, **kw):
    rt = MeetingRuntime(max_proposal_age_ms=kw.pop("max_age", 5000), **kw)
    for s in (State.CONNECTING, State.LISTENING, State.EVALUATING_TURN):
        rt.transition(s)
        if s is state:
            break
    return rt


def utt(pid="r", conf=0.99, overlap=False, eid="e1", pause=0, text="hi"):
    return Utterance(eid, text, Attribution(pid, conf, overlap), pause)


POLICY = AppPolicy(allowed_tools=frozenset({"crm_note"}))
ROLES = {"a": Role.CANDIDATE, "b": Role.CANDIDATE, "r": Role.RECRUITER}
__all__ = ["ToolSpec"]
