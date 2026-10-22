from tara.core.config import AppPolicy, Mode, ParticipantSpec, Role, SpeakingPolicy, ToolSpec, validate
from tests.helpers import POLICY, cfg, group_cfg


def fields(res):
    return {i.field for i in res.errors()}


def test_valid_engineering_meeting():
    assert validate(cfg(), POLICY).ok


def test_group_interview_valid():
    assert validate(group_cfg(), POLICY).ok


def test_missing_disclosure_blocks():
    assert "disclosure_text" in fields(validate(cfg(disclosure_text=" "), POLICY))


def test_group_interview_needs_threshold():
    c = group_cfg()
    c = type(c)(**{**c.__dict__, "attribution_min_confidence": None})
    assert "attribution_min_confidence" in fields(validate(c, POLICY))


def test_group_interview_single_candidate_rejected():
    ps = (ParticipantSpec("a", "A", Role.CANDIDATE),)
    c = cfg(mode=Mode.GROUP_INTERVIEW, policy=SpeakingPolicy.MODERATED, participants=ps)
    assert "participants" in fields(validate(c, POLICY))


def test_interview_without_candidate_rejected():
    assert "participants" in fields(validate(cfg(mode=Mode.INTERVIEW_STRUCTURED), POLICY))


def test_observe_requires_silent():
    assert "policy" in fields(validate(cfg(mode=Mode.OBSERVE), POLICY))
    assert validate(cfg(mode=Mode.OBSERVE, policy=SpeakingPolicy.SILENT), POLICY).ok


def test_proactive_blocked_by_app_policy():
    c = cfg(policy=SpeakingPolicy.PROACTIVE_ALLOWED)
    assert "policy" in fields(validate(c, AppPolicy(allow_proactive=False)))


def test_proactive_in_interview_contradiction():
    c = group_cfg()
    c = type(c)(**{**c.__dict__, "policy": SpeakingPolicy.PROACTIVE_ALLOWED})
    assert "policy" in fields(validate(c, POLICY))


def test_audio_retention_needs_policy():
    assert "retain_audio" in fields(validate(cfg(retain_audio=True), POLICY))


def test_pitch_requires_knowledge():
    assert "knowledge" in fields(validate(cfg(mode=Mode.PITCH), POLICY))
    assert validate(cfg(mode=Mode.PITCH, approved_knowledge_ids=("docs",)), POLICY).ok


def test_prose_cannot_grant_tools():
    c = cfg(tools=(ToolSpec("crm_note", "append"), ToolSpec("send_email", "any", False)))
    res = validate(c, POLICY)
    assert res.ok
    assert [t.name for t in res.effective_tools] == ["crm_note"]
    assert any("send_email" in i.message for i in res.issues)


def test_bad_schema_and_duplicate_refs():
    ps = (ParticipantSpec("a", "X", Role.CANDIDATE), ParticipantSpec("a", "Y", Role.CANDIDATE))
    res = validate(cfg(schema="other", participants=ps), POLICY)
    assert {"schema", "participants"} <= fields(res)


def test_threshold_range():
    c = group_cfg()
    c = type(c)(**{**c.__dict__, "attribution_min_confidence": 1.5})
    assert "attribution_min_confidence" in fields(validate(c, POLICY))
