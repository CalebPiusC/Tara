"""Deterministic time tools: IANA zones, DST edges, and explicit failures."""
from __future__ import annotations

import pytest

from tara.errors import ToolError
from tara.orchestration.time import convert_time, get_time, time_tool_defs, time_until


def test_iana_offsets_and_utc_conversion():
    lagos = get_time("Africa/Lagos", "2026-10-20T12:00")
    assert lagos["utc_time"] == "2026-10-20T11:00:00+00:00"
    assert lagos["utc_offset"] == "+01:00"
    assert lagos["dst"] is False
    ny = get_time("America/New_York", "2026-10-20T12:00")
    assert ny["utc_offset"] == "-04:00" and ny["dst"] is True
    assert ny["utc_time"] == "2026-10-20T16:00:00+00:00"


def test_dst_spring_forward_and_fall_back_are_explicit():
    before = convert_time("2026-03-08T01:30", "America/New_York", "UTC")
    assert before["utc"] == "2026-03-08T06:30:00+00:00"
    assert before["source"]["abbreviation"] == "EST"
    assert before["ambiguous_source"] is False

    with pytest.raises(ToolError) as missing:
        convert_time("2026-03-08T02:30", "America/New_York", "UTC")
    assert missing.value.code == "nonexistent_time"

    fall = convert_time("2026-11-01T01:30", "America/New_York", "UTC")
    assert fall["ambiguous_source"] is True
    assert fall["source"]["abbreviation"] == "EDT"      # fold 0: the earlier occurrence


def test_invalid_zone_and_bad_time_are_structured_failures():
    with pytest.raises(ToolError) as zone:
        get_time("Mars/Olympus_Mons")
    assert zone.value.code == "unknown_timezone"
    with pytest.raises(ToolError) as bad:
        get_time("UTC", "tomorrow afternoon")
    assert bad.value.code == "bad_time"
    with pytest.raises(ToolError) as clock:
        get_time("UTC", "25:00")
    assert clock.value.code == "bad_time"


def test_time_until_reports_past_and_human_duration():
    future = time_until("2099-01-01T00:00:00+00:00")
    assert future["past"] is False and future["seconds"] > 0
    past = time_until("2000-01-01T00:00:00+00:00")
    assert past["past"] is True and "ago" in past["human"]


def test_time_tool_declarations_are_read_only_and_schema_bearing():
    defs = {d.name: d for d in time_tool_defs()}
    assert set(defs) == {"get_time", "convert_time", "time_until"}
    assert all(not d.side_effect and d.category == "time" for d in defs.values())
    assert defs["get_time"].parameters_schema is not None
    assert "timezone" in defs["get_time"].parameters_schema["properties"]
