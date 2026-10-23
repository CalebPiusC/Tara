"""Retrieval query parsing and ranking for long-term memory.

Retrieval is hybrid but deliberately cheap: PostgreSQL full-text search proposes candidates, then this module
re-ranks them with several independent signals (keyword rarity, phrase match, confirmation status, kind,
recency, conflict flag) and removes near-duplicates. No embedding model and no second query per turn.

`rank_hits` is pure and deterministic when given `now`, so the behaviour is testable offline. The store owns
scope and authorization; nothing here can widen a scope or see another tenant.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .models import MemoryHit, MemoryItem, is_expired, normalize_text, tokens

# Weights are starting values to tune from measured retrieval results, not measured quality claims.
W_FTS = 0.35
W_COVERAGE = 0.25
W_PHRASE = 0.10
W_RECENCY = 0.15
RECENCY_HALF_LIFE_DAYS = 45.0

STATUS_BOOST = {"confirmed": 0.08, "candidate": 0.0, "superseded": -10.0}
KIND_BOOST = {
    "decision": 0.08, "action": 0.10, "correction": 0.08, "preference": 0.04,
    "project": 0.05, "fact": 0.03, "summary": 0.02, "note": 0.0, "episode": -0.06,
}
DIVERSITY_SIMILARITY = 0.85

_PERIOD_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bthe day before yesterday\b", re.I), "-2d"),
    (re.compile(r"\byesterday\b", re.I), "yesterday"),
    (re.compile(r"\btoday\b", re.I), "today"),
    (re.compile(r"\blast week\b", re.I), "7d"),
    (re.compile(r"\bthis week\b", re.I), "week"),
    (re.compile(r"\blast month\b", re.I), "30d"),
    (re.compile(r"\bthis month\b", re.I), "month"),
    (re.compile(r"\blast (\d{1,2}) (day|days|week|weeks|month|months)\b", re.I), "n"),
)

_KIND_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(decision|decisions|decide|decided|agreed|settled)\b", re.I), "decision"),
    (re.compile(r"\b(action item|action items|actions|to-?do|commitment|commitments|promise|promises)\b", re.I), "action"),
    (re.compile(r"\b(preference|preferences|prefer|prefers)\b", re.I), "preference"),
    (re.compile(r"\b(summary|summaries|summarise|summarize|recap)\b", re.I), "summary"),
    (re.compile(r"\b(correction|corrected|corrections)\b", re.I), "correction"),
)


@dataclass(frozen=True)
class Query:
    """A spoken question split into text plus explicit filters. Filters are optional."""

    text: str
    since: datetime | None = None
    until: datetime | None = None
    period_label: str = ""
    kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankedHit:
    item: MemoryItem
    score: float
    reason: str = ""
    matched: tuple[str, ...] = ()


def _day_start(now: datetime) -> datetime:
    local = now.astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def parse_query(query: str, *, now: datetime | None = None) -> Query:
    """Split 'what did we decide about auth last week?' into text + a time window + kind hints."""
    now = now or datetime.now(UTC)
    text = " ".join(str(query).split())
    since: datetime | None = None
    until: datetime | None = None
    label = ""
    for pattern, kind in _PERIOD_PATTERNS:
        m = pattern.search(text)
        if m is None:
            continue
        if kind == "today":
            since = _day_start(now)
            label = "today"
        elif kind == "yesterday":
            start = _day_start(now)
            since, until = start - timedelta(days=1), start
            label = "yesterday"
        elif kind == "-2d":
            start = _day_start(now)
            since, until = start - timedelta(days=2), start - timedelta(days=1)
            label = "the day before yesterday"
        elif kind in ("7d", "30d"):
            days = int(kind[:-1])
            since = now - timedelta(days=days)
            label = f"the last {days} days"
        elif kind in ("week", "month"):
            days = now.weekday() if kind == "week" else now.day - 1
            since = _day_start(now) - timedelta(days=days)
            label = f"this {kind}"
        elif kind == "n":
            n = max(1, min(int(m.group(1)), 52))
            unit = m.group(2).lower().rstrip("s")
            days = n * {"day": 1, "week": 7, "month": 30}[unit]
            since = now - timedelta(days=days)
            label = f"the last {n} {unit}{'' if n == 1 else 's'}"
        text = (text[: m.start()] + " " + text[m.end():]).strip()
        break
    hints: list[str] = []
    for pattern, kind in _KIND_HINTS:
        if pattern.search(text) and kind not in hints:
            hints.append(kind)
    # "decide" appears in the question but not in the stored decision wording; keep it as a filter, not a keyword.
    return Query(text=text, since=since, until=until, period_label=label, kinds=tuple(hints))


def rank_hits(hits: list[MemoryHit], query: str, *, now: datetime | None = None, limit: int = 8,
              half_life_days: float = RECENCY_HALF_LIFE_DAYS,
              kinds: tuple[str, ...] = ()) -> list[RankedHit]:
    """Re-rank candidate hits, drop duplicates, and return at most `limit`."""
    now = now or datetime.now(UTC)
    q = tokens(query)
    phrase = normalize_text(query)
    pool = [h for h in hits if h.item.status != "superseded" and not is_expired(h.item, now)]
    if kinds:
        pool = [h for h in pool if h.item.kind in kinds]
    if not pool:
        return []
    doc_tokens = {h.item.id: tokens(h.item.text) for h in pool}
    df = Counter(t for ts in doc_tokens.values() for t in ts)
    idf_total = sum(1.0 / math.log(2 + df[t]) for t in q) or 1.0
    scored: list[RankedHit] = []
    for h in pool:
        item = h.item
        ts = doc_tokens[item.id]
        matched = q & ts
        rare = sum(1.0 / math.log(2 + df[t]) for t in matched)
        coverage = rare / idf_total if q else 0.0
        if h.reason == "fts":
            fts = min(1.0, max(0.0, h.score) / 1.5)
        elif h.reason == "keyword":
            fts = min(1.0, max(0.0, h.score) / 8.0)
        else:
            fts = 0.0
        phrase_boost = W_PHRASE if phrase and len(phrase) > 3 and phrase in normalize_text(item.text) else 0.0
        age_days = max(0.0, (now - item.updated_at).total_seconds() / 86400.0)
        recency = 0.5 ** (age_days / max(1.0, half_life_days))
        score = (W_FTS * fts + W_COVERAGE * coverage + phrase_boost + W_RECENCY * recency
                 + STATUS_BOOST.get(item.status, 0.0) + KIND_BOOST.get(item.kind, 0.0))
        if item.metadata.get("possible_conflict"):
            score -= 0.03
        if item.kind == "episode":
            score -= 0.04
        reason = [h.reason] if h.reason else []
        if coverage:
            reason.append("keywords")
        if phrase_boost:
            reason.append("phrase")
        if item.status == "confirmed":
            reason.append("confirmed")
        scored.append(RankedHit(item, round(score, 6), "+".join(reason), tuple(sorted(matched))))
    scored.sort(key=lambda r: (-r.score, -r.item.updated_at.timestamp(), r.item.id))
    chosen: list[RankedHit] = []
    for cand in scored:
        cts = doc_tokens[cand.item.id]
        if any(_similar(cts, doc_tokens[c.item.id]) >= DIVERSITY_SIMILARITY for c in chosen):
            continue
        chosen.append(cand)
        if len(chosen) >= max(1, limit):
            break
    return chosen


def _similar(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def claim_label(item: MemoryItem) -> str:
    """Describe provenance without implying that repetition equals agreement."""
    if item.kind == "episode":
        return "transcript"
    if item.kind == "summary":
        return "generated summary"
    if item.metadata.get("tentative"):
        return "proposal, not agreed"
    if item.metadata.get("possible_conflict"):
        return "conflicts with another memory"
    if item.metadata.get("explicit_save"):
        return "explicitly saved on request"
    if item.kind == "note" and item.metadata.get("minutes"):
        return "generated meeting summary"
    if item.status == "confirmed" and item.metadata.get("auto"):
        return "repeatedly mentioned, not explicitly agreed"
    return "confirmed" if item.status == "confirmed" else "mentioned, not confirmed"
