"""Deterministic time and time-zone tools.

No web search and no model call: IANA zones and the standard library do the work.
Ambiguous/nonexistent local times are reported rather than guessed.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..errors import ToolError
from ..tools import ToolDef

_CLOCK = re.compile(r"^\d{1,2}:\d{2}$")


def _zone(name: str) -> ZoneInfo:
    value = (name or "").strip()
    if not value or len(value) > 80:
        raise ToolError("unknown_timezone", "A valid IANA time zone is required")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ToolError("unknown_timezone", f"Unknown IANA time zone: {value}") from exc


def _parse_at(value: str, tz: ZoneInfo) -> tuple[datetime, bool]:
    """Return an aware datetime and whether the local wall time is ambiguous."""
    text = (value or "").strip()
    if not text:
        return datetime.now(tz), False
    if _CLOCK.match(text):
        hour, minute = (int(x) for x in text.split(":"))
        if hour > 23 or minute > 59:
            raise ToolError("bad_time", "Time must be a valid 24-hour clock value")
        dt = datetime.combine(datetime.now(tz).date(), datetime.min.time().replace(hour=hour, minute=minute), tz)
    else:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ToolError("bad_time", "Time must be ISO-8601 or HH:MM") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    ambiguous = False
    if dt.tzinfo is tz or getattr(dt.tzinfo, "key", None) == tz.key:
        before = dt.replace(fold=0)
        after = dt.replace(fold=1)
        ambiguous = before.utcoffset() != after.utcoffset()
        round_trip = dt.astimezone(UTC).astimezone(tz)
        if (round_trip.year, round_trip.month, round_trip.day, round_trip.hour, round_trip.minute) != (
                dt.year, dt.month, dt.day, dt.hour, dt.minute):
            raise ToolError("nonexistent_time", "That local time does not exist because of a daylight-saving change")
    return dt, ambiguous


def _offset_text(dt: datetime) -> str:
    seconds = int((dt.utcoffset() or timedelta()).total_seconds())
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    return f"{sign}{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}"


def _time_payload(dt: datetime, tz: ZoneInfo, ambiguous: bool) -> dict[str, object]:
    return {
        "timezone": tz.key,
        "local_time": dt.isoformat(),
        "utc_time": dt.astimezone(UTC).isoformat(),
        "date": dt.date().isoformat(),
        "weekday": dt.strftime("%A"),
        "utc_offset": _offset_text(dt),
        "utc_offset_seconds": int((dt.utcoffset() or timedelta()).total_seconds()),
        "abbreviation": dt.tzname() or "",
        "dst": bool(dt.dst() and dt.dst() != timedelta(0)),
        "ambiguous": ambiguous,
    }


def get_time(timezone: str, at: str = "") -> dict[str, object]:
    tz = _zone(timezone)
    dt, ambiguous = _parse_at(at, tz)
    return _time_payload(dt, tz, ambiguous)


def convert_time(time: str, from_timezone: str, to_timezone: str) -> dict[str, object]:
    source_zone, target_zone = _zone(from_timezone), _zone(to_timezone)
    source, ambiguous = _parse_at(time, source_zone)
    target = source.astimezone(target_zone)
    return {
        "source": _time_payload(source, source_zone, ambiguous),
        "target": _time_payload(target, target_zone, False),
        "utc": source.astimezone(UTC).isoformat(),
        "ambiguous_source": ambiguous,
    }


def time_until(target: str, timezone: str = "UTC") -> dict[str, object]:
    tz = _zone(timezone)
    when, _ = _parse_at(target, tz)
    now = datetime.now(tz)
    delta = when - now
    seconds = int(delta.total_seconds())
    past = seconds < 0
    remaining = abs(seconds)
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes = remaining // 60
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    human = ", ".join(parts) + (" ago" if past else " from now")
    return {
        "target": when.isoformat(),
        "now": now.isoformat(),
        "timezone": tz.key,
        "seconds": seconds,
        "days": days,
        "hours": hours,
        "minutes": minutes,
        "human": human,
        "past": past,
    }


def time_tool_defs() -> tuple[ToolDef, ...]:
    schemas = {
        "get_time": {
            "type": "OBJECT",
            "properties": {
                "timezone": {"type": "STRING", "description": "IANA zone, e.g. Africa/Lagos or Asia/Tokyo"},
                "at": {"type": "STRING", "description": "Optional ISO-8601 datetime or HH:MM; default now"},
            },
            "required": ["timezone"],
        },
        "convert_time": {
            "type": "OBJECT",
            "properties": {
                "time": {"type": "STRING", "description": "ISO-8601 datetime or HH:MM in the source zone"},
                "from_timezone": {"type": "STRING", "description": "IANA source zone"},
                "to_timezone": {"type": "STRING", "description": "IANA target zone"},
            },
            "required": ["time", "from_timezone", "to_timezone"],
        },
        "time_until": {
            "type": "OBJECT",
            "properties": {
                "target": {"type": "STRING", "description": "ISO-8601 target datetime"},
                "timezone": {"type": "STRING", "description": "IANA zone for a naive target; default UTC"},
            },
            "required": ["target"],
        },
    }
    return (
        ToolDef("get_time", {"timezone": str}, get_time, optional={"at": str},
                description="Get the exact local time, date, UTC offset and DST status in an IANA time zone.",
                timeout_s=3.0, permission="time_read", side_effect=False, category="time",
                parameters_schema=schemas["get_time"]),
        ToolDef("convert_time", {"time": str, "from_timezone": str, "to_timezone": str}, convert_time,
                description="Convert a local time between IANA time zones, including daylight-saving rules.",
                timeout_s=3.0, permission="time_read", side_effect=False, category="time",
                parameters_schema=schemas["convert_time"]),
        ToolDef("time_until", {"target": str}, time_until, optional={"timezone": str},
                description="Calculate the exact duration until an ISO-8601 target time; say when it is past.",
                timeout_s=3.0, permission="time_read", side_effect=False, category="time",
                parameters_schema=schemas["time_until"]),
    )
