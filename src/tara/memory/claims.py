"""What kind of claim is this sentence? Deterministic, English-only, conservative.

The extractor must not turn a proposal ("we should probably use PostgreSQL") into a decision. This module
classifies wording only: it never decides truth, and its output is a label attached to a candidate, never a
promotion. Order matters: a correction outranks a decision ("actually, we decided X" is a correction), and a
question never becomes a decision.
"""
from __future__ import annotations

import re
from enum import Enum


class Claim(str, Enum):
    ASSERTION = "assertion"
    PREFERENCE = "preference"
    DECISION = "decision"
    PROPOSAL = "proposal"
    ACTION = "action"
    QUESTION = "question"
    HYPOTHETICAL = "hypothetical"
    CORRECTION = "correction"


_R = lambda p: re.compile(p, re.I)  # noqa: E731

_CORRECTION = _R(r"\bactually,? (it'?s|it is|it was|that'?s|that is|the|we)\b|\bcorrection\b|"
                 r"\bthat'?s (wrong|not right|incorrect)\b|\bi (meant|misspoke)\b|"
                 r"\bnot [\w -]{2,30},? (it'?s|but)\b")
# "We have decided", "we agreed", "it's settled" — committed wording only. "We will" and "let's go with" are not
# included: they are plans or proposals until someone says they were agreed.
_DECISION = _R(r"\bwe (have )?(decided|agreed|settled|chose|resolved)\b|\bthe decision is\b|"
               r"\bit'?s (decided|settled|agreed)\b|\bwe'?ve (decided|agreed|settled|chosen)\b|"
               r"\bfinal decision\b|\bwe are going with\b|\bwe'?re going with\b")
_PROPOSAL = _R(r"\bwe (should|could|might|may|can)\b|\bi (suggest|propose|recommend)\b|\blet'?s (consider|think about|try)\b|"
               r"\bwhat about\b|\bhow about\b|\bmaybe\b|\bperhaps\b|\bprobably\b|\bperhaps\b|\bidea is\b|"
               r"\bwe'?ll (use|go with|ship|adopt|drop)\b|\bwe will (use|go with|ship|adopt|drop)\b")
_HYPOTHETICAL = _R(r"\bif (we|you|they|i)\b|\bwhat if\b|\bimagine\b|\bsuppose\b|\bwould (be|have|use)\b|"
                   r"\bcould have\b|\bin theory\b|\bhypothetically\b")
_ACTION = _R(r"\baction item\b|\bnext steps?\b|\bto-?do\b|\bfollow[- ]?up\b|"
             r"\b(i'?ll|i will) (send|write|check|look into|prepare|fix|own|handle|review|schedule|share|update|draft)\b|"
             r"\b(can|could|will) you (please )?(send|write|check|look|prepare|fix|handle|review|schedule|share|update|draft)\b|"
             r"\bby (monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|tonight|next week|"
             r"end of (day|week|month)|eod|eow)\b|\bdeadline\b")
_PREFERENCE = _R(r"\b(i|we) (always|usually|never|prefer|like|dislike|hate|love|avoid|tend to)\b|"
                 r"\bmy (favou?rite|preferred)\b|\bplease (always|never)\b|\bi'?d rather\b")


def is_tentative(text: str) -> bool:
    """Wording that cannot support a confirmed fact or an agreed decision."""
    return bool(_PROPOSAL.search(text) or _HYPOTHETICAL.search(text))


def classify_claim(text: str) -> Claim:
    t = " ".join(str(text).split())
    if not t:
        return Claim.ASSERTION
    if _CORRECTION.search(t):
        return Claim.CORRECTION
    if _DECISION.search(t):
        return Claim.DECISION
    if _HYPOTHETICAL.search(t):
        return Claim.HYPOTHETICAL
    if t.endswith("?"):
        return Claim.QUESTION
    if _PROPOSAL.search(t):
        return Claim.PROPOSAL
    if _ACTION.search(t):
        return Claim.ACTION
    if _PREFERENCE.search(t):
        return Claim.PREFERENCE
    return Claim.ASSERTION


def durable_kind(text: str, proposed: str) -> tuple[str, Claim]:
    """The kind a candidate should actually be stored under, plus the claim label.

    A proposal or hypothetical never keeps the `decision` kind. It is kept as a `note` candidate so the
    evidence is not lost, but retrieval can label it as not agreed.
    """
    claim = classify_claim(text)
    kind = proposed
    if kind == "decision" and claim in (Claim.PROPOSAL, Claim.HYPOTHETICAL, Claim.QUESTION):
        kind = "note"
    if claim in (Claim.PROPOSAL, Claim.HYPOTHETICAL) and kind == "fact":
        kind = "note"
    if claim is Claim.CORRECTION and kind not in ("correction", "note"):
        kind = "correction"
    return kind, claim
