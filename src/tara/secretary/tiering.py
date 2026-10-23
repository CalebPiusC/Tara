"""What should be kept, and for how long? Deterministic, English-only, deliberately conservative.

Tiers:
  EPHEMERAL  nothing is stored beyond the live working memory (small talk, questions, anything sensitive)
  SESSION    a meeting note candidate (decision, action item, open question) in this meeting's memory
  LONG_TERM  a long-term memory CANDIDATE with an expiry; it becomes permanent only when seen again or confirmed

Auto-saved items are never "confirmed" on their own. Explicit "remember this" requests keep using the existing
explicit-memory path. Sensitive content is never auto-saved at any tier. These are cues with false positives and
false negatives; the cost of a wrong guess is bounded (candidates expire, nothing is spoken as fact).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ..memory.claims import Claim, classify_claim


class Tier(str, Enum):
    EPHEMERAL = "ephemeral"
    SESSION = "session"
    LONG_TERM = "long_term"


@dataclass(frozen=True)
class TierDecision:
    tier: Tier
    kind: str = ""               # memory kind (long term) or note kind (session)
    reason: str = ""
    needs_speaker: bool = False  # the fact is about the speaker: skip it when the speaker is unknown
    supersedes: bool = False     # a correction of earlier information


_R = lambda p: re.compile(p, re.I)  # noqa: E731
_SENSITIVE = _R(
    r"\b(password|passcode|passphrase|api[ _-]?key|secret key|private key|access token|bearer|otp|one[- ]time code|"
    r"pin number|cvv|ssn|social security|national id|passport (number|no)|bank account|account number|iban|sort code|"
    r"credit card|card number|diagnos\w+|prescription|medication|therapy|pregnan\w+|disabilit\w+|religio\w+|"
    r"church|mosque|ethnic\w*|race|sexual orientation|gay|lesbian|transgender|political (party|views)|union member|"
    r"immigration status|criminal record|salary|date of birth|dob)\b")
_SECRET_SHAPES = (_R(r"\bAIza[0-9A-Za-z_-]{20,}\b"), _R(r"\btvly-[A-Za-z0-9_-]{10,}\b"), _R(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
                  _R(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), _R(r"\b[A-Fa-f0-9]{32,}\b"), _R(r"\b(?:\d[ -]?){13,19}\b"))
_PREFERENCE = _R(r"\b(i|we) (always|usually|never|prefer|like|dislike|hate|love|avoid|tend to)\b|\bmy (favou?rite|preferred)\b|"
                 r"\bplease (always|never)\b|\bi'?d rather\b")
_STANDING = _R(r"\bmy name is\b|\bi (work|am working) (at|for|as)\b|\bi'?m (the|a|an) [\w -]{2,40} (at|of|for)\b|"
               r"\bour (company|team|product|stack|client|customer|project)( is| are| uses?| called)\b|\bwe (use|run|are building|are hiring|"
               r"are migrating|deploy|support)\b|\bmy (role|title|team|manager|timezone|time zone) is\b")
_DECISION = _R(r"\bwe (have )?(decided|agreed|settled|chose|resolved)\b|\bthe decision is\b|\blet'?s go with\b|"
               r"\bit'?s (decided|settled|agreed)\b|\bwe'?re going (with|to)\b|\bwe will (use|go with|ship|adopt|drop)\b")
_PROPOSAL = _R(r"\bwe (should|could|might|may)\b|\bi (suggest|propose|recommend)\b|\blet'?s (consider|try)\b|"
               r"\bwhat about\b|\bhow about\b|\bmaybe\b|\bperhaps\b|\bprobably\b")
_ACTION = _R(r"\baction item\b|\bnext steps?\b|\bto-?do\b|\bfollow[- ]?up\b|\bi'?ll (send|write|check|look into|prepare|fix|own|"
             r"handle|review|schedule|share|update|draft|follow up)\b|\b(can|could|will) you (please )?(send|write|check|look|prepare|fix|"
             r"handle|review|schedule|share|update|draft)\b|\b(by|before|due) (monday|tuesday|wednesday|thursday|friday|saturday|"
             r"sunday|tomorrow|tonight|next week|end of (day|week|month)|eod|eow)\b|\bdeadline\b")
_OPEN_Q = _R(r"\bopen question\b|\bwe still (need|have) to (decide|figure|find|confirm)\b|\bnot sure (yet )?(whether|if|who|when|how)\b|"
             r"\bwe (don'?t|do not) know (yet )?(whether|if|who|when|how)\b|\bto be decided\b|\btbd\b|\bparking lot\b")
_CORRECTION = _R(r"\bactually,? (it'?s|it is|it was|that'?s|that is|the|we)\b|\bcorrection\b|\bthat'?s (wrong|not right|incorrect),? (it'?s|it was)\b|"
                 r"\bi (meant|misspoke)\b|\bnot [\w -]{2,30},? (it'?s|but)\b")
_SMALLTALK = _R(r"^\s*(ok(ay)?|yes|yeah|yep|no|nope|sure|right|thanks?( you)?|thank you|great|cool|nice|hello|hi|hey|bye|good "
                r"(morning|afternoon|evening)|got it|i see|makes sense|mm+|uh+|um+|haha+|lol)\W*$")


def contains_sensitive(text: str) -> bool:
    return bool(_SENSITIVE.search(text)) or any(rx.search(text) for rx in _SECRET_SHAPES)


def classify_turn(text: str) -> TierDecision:
    """Decide the tier for one participant turn. Sensitive content always wins and is never stored."""
    t = " ".join(text.split())
    if not t:
        return TierDecision(Tier.EPHEMERAL, reason="empty")
    if contains_sensitive(t):
        return TierDecision(Tier.EPHEMERAL, reason="sensitive")
    words = len(t.split())
    if _SMALLTALK.match(t) or words < 4:
        return TierDecision(Tier.EPHEMERAL, reason="too short or small talk")
    if t.endswith("?") and not _DECISION.search(t):
        return TierDecision(Tier.EPHEMERAL, reason="question")
    claim = classify_claim(t)
    if claim is Claim.CORRECTION or _CORRECTION.search(t):
        return TierDecision(Tier.LONG_TERM, "correction", "correction of earlier information", supersedes=True)
    if _OPEN_Q.search(t):
        return TierDecision(Tier.SESSION, "open_question", "open question wording")
    if claim is Claim.DECISION:
        return TierDecision(Tier.SESSION, "decision", "decision wording")
    if claim is Claim.PROPOSAL or _PROPOSAL.search(t):
        return TierDecision(Tier.SESSION, "proposal", "proposal, not an agreed decision")
    if claim is Claim.ACTION or _ACTION.search(t):
        return TierDecision(Tier.SESSION, "action_item", "action or deadline wording")
    if claim is Claim.PREFERENCE or _PREFERENCE.search(t):
        return TierDecision(Tier.LONG_TERM, "preference", "stated preference", needs_speaker=True)
    if _STANDING.search(t):
        return TierDecision(Tier.LONG_TERM, "fact", "standing fact about the speaker, team or project",
                            needs_speaker=bool(re.search(r"\b(my|i'?m|i am|i work)\b", t, re.I)))
    return TierDecision(Tier.EPHEMERAL, reason="no durable cue")
