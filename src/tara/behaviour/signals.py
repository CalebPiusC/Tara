"""Lightweight lexical reading of what participants just said. Deterministic, English only,
no model call. These are cues with false positives and negatives: they steer STYLE, never permissions.
The word lists are detection vocabulary for participants' speech, not scripted lines for Tara."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..classify import name_hint

_R = lambda p: re.compile(p, re.I)  # noqa: E731
_LAUGH = _R(r"\b(ha(ha)+|hehe+|lol|lmao|rofl)\b|\[laugh|\(laugh|😂|🤣")
_JOKE = _R(r"\b(just kidding|jk|kidding|joking|joke|funny|pun|hilarious|plot twist)\b")
_FRUSTRATION = _R(r"\b(frustrat\w*|annoy\w*|ugh+|sick of|tired of|fed up|waste of time|keeps? (breaking|failing|crashing)|again\?!|so slow|driving me (crazy|nuts))\b")
_BAD_NEWS = _R(r"\b(unfortunately|bad news|didn'?t get|did not get|rejected|laid off|failed|we lost|cancel(l)?ed|didn'?t make it|fell through|missed the deadline|delayed|disappointed|disappointing)\b")
_CONFUSION = _R(r"\b(i'?m lost|don'?t understand|do not understand|confus\w*|what do you mean|not sure what you mean|i don'?t follow|lost me)\b")
_CORRECTION = _R(r"\b(no,? i meant|i meant|actually,? (it'?s|it was|that'?s)|that'?s not what i (said|meant)|correction|i misspoke|scratch that|not quite,|you misunderstood)\b")
_URGENT = _R(r"\b(outage|incident|urgent\w*|asap|production is|prod is|p0|sev ?[0-3]|on fire|rollback now|customers? (are )?(affected|impacted)|site is down|is down|breach|emergency)\b")
_CONFLICT = _R(r"\b(that'?s (wrong|ridiculous|nonsense|unacceptable)|you never|you always|i strongly disagree|stop interrupting|your fault|blame|not what we agreed)\b")
_SENSITIVE = _R(r"\b(passed away|funeral|diagnos\w*|cancer|divorce|pregnan\w*|miscarr\w*|laid off|got fired|harass\w*|mental health|surgery|bereave\w*|grief)\b")
_UNEXPECTED = _R(r"\b(out of nowhere|turns out|didn'?t see that coming|believe it|no way|suddenly|shockingly|surprisingly|wait,? what)\b")
_FORMAL = _R(r"\b(sir|madam|dear|regards|please be advised|kindly|pursuant|respectfully)\b")
_CASUAL = _R(r"\b(gonna|wanna|gotta|yeah|yep|nope|hey|dude|kinda|sorta|lol|btw|cool)\b")
_DETAIL = _R(r"\b(explain|walk me through|in detail|elaborate|step by step|why does|how does|go deeper|break (it|this) down)\b")
_BRIEF = _R(r"\b(quick(ly)?|briefly|in short|tl;?dr|one line|short answer|yes or no)\b")
_QUESTION_START = _R(r"^\s*(tara,?\s*)?(who|what|when|where|why|how|can|could|would|will|should|do|does|did|is|are|was)\b")


@dataclass(frozen=True)
class Signals:
    laughter: bool = False
    joking: bool = False
    frustration: bool = False
    bad_news: bool = False
    confusion: bool = False
    correction: bool = False
    urgency: bool = False
    conflict: bool = False
    sensitive: bool = False
    unexpected: bool = False
    asks_detail: bool = False
    asks_brief: bool = False
    is_question: bool = False
    addressed_by_name: bool = False
    formality: str = "neutral"
    words: int = 0

    @property
    def playful(self) -> bool:
        return self.laughter or self.joking

    @property
    def heavy(self) -> bool:
        """Anything that makes humour inappropriate."""
        return self.urgency or self.conflict or self.sensitive or self.bad_news or self.frustration


def analyze(text: str, names: tuple[str, ...] = ("tara",)) -> Signals:
    t = text or ""
    informal, formal = bool(_CASUAL.search(t)), bool(_FORMAL.search(t))
    return Signals(
        laughter=bool(_LAUGH.search(t)), joking=bool(_JOKE.search(t)), frustration=bool(_FRUSTRATION.search(t)),
        bad_news=bool(_BAD_NEWS.search(t)), confusion=bool(_CONFUSION.search(t)), correction=bool(_CORRECTION.search(t)),
        urgency=bool(_URGENT.search(t)), conflict=bool(_CONFLICT.search(t)), sensitive=bool(_SENSITIVE.search(t)),
        unexpected=bool(_UNEXPECTED.search(t)), asks_detail=bool(_DETAIL.search(t)), asks_brief=bool(_BRIEF.search(t)),
        is_question=("?" in t) or bool(_QUESTION_START.search(t)), addressed_by_name=name_hint(t, names)[0],
        formality="formal" if formal and not informal else "casual" if informal and not formal else "neutral",
        words=len(t.split()))
