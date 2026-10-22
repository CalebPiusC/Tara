"""The local voice demo must build a valid meeting config without bypassing policy."""
from tara.core.config import AppPolicy, validate
from tara.voice.demo import _meeting


def test_demo_meeting_config_is_valid():
    res = validate(_meeting("conversational"), AppPolicy())
    assert res.errors() == []
    cfg = _meeting("conversational")
    assert cfg.policy.value == "on_address_only"
    assert cfg.behaviour_profile == "conversational"


def test_one_on_one_demo_policy_is_proactive_but_still_gated_by_mode():
    cfg = _meeting("professional", one_on_one=True)
    assert validate(cfg, AppPolicy()).errors() == []
    assert cfg.policy.value == "proactive_allowed"
    assert cfg.mode.value == "natural"                     # interviews must never use this policy
