"""Quality checks for Tara's OWN words (from output transcription). Heuristic, English only.
Used for runtime metrics and as the rubric of offline/live evaluation. They flag; they never edit or block speech."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .profiles import VERBOSITY

_R = lambda p: re.compile(p, re.I)  # noqa: E731
CANNED_OPENERS = _R(r"^\W*(that'?s|what) an? (great|good|excellent|interesting|fantastic|wonderful) (question|point|observation)|^\W*(absolutely|certainly|definitely|of course)\b[!.,]|^\W*i (completely|totally|fully) understand|^\W*i would be (happy|glad|delighted) to (assist|help)|^\W*as an ai\b|^\W*thank you for (asking|your question)")
AI_DISCLAIMER = _R(r"\bas an ai( language model)?\b|\bi am (just )?an ai language model\b")
HUMAN_EXPERIENCE = _R(r"\bi (remember when i|grew up|used to live|went to (school|college) (in|at)|had (a|my) (weekend|childhood|coffee|lunch|breakfast|dinner)|felt (so )?(tired|hungry|nervous|exhausted|sick))\b|\bmy (weekend|childhood|kids|children|wife|husband|mom|dad|mother|father|family|house|commute)\b|\blast (weekend|night) i\b|\bwhen i was (a kid|young|in (school|college))\b")
FORCED_LAUGH = _R(r"\b(ha ?){3,}|\bhehe(he)+\b|\blol\b")
ORDINAL_LIST = _R(r"\b(firstly|first of all|secondly|thirdly|fourthly)\b")
STAGE_DIRECTION = _R(r"\[[a-z ]{3,20}\]|\*[a-z ]{3,20}\*|\((laughs|sighs|chuckles|smiles)\)")

MAX_WORDS = {"brief": 35, "concise": 70, "detailed": 180}   # starting limits for spoken answers, to tune with real sessions


@dataclass(frozen=True)
class Flag:
    name: str
    detail: str = ""


def _opener(text: str, n: int = 3) -> str:
    return " ".join(re.findall(r"[a-z']+", text.lower())[:n])


def restates_question(text: str, question: str, run: int = 6) -> bool:
    a, b = re.findall(r"[a-z']+", text.lower()), re.findall(r"[a-z']+", question.lower())
    if len(b) < run:
        return False
    grams = {" ".join(b[i:i + run]) for i in range(len(b) - run + 1)}
    return any(" ".join(a[i:i + run]) in grams for i in range(max(0, min(len(a) - run + 1, 12))))   # only near the start


def flag_text(text: str, verbosity: str = "concise", question: str = "", laughter_allowed: bool = False,
              recent_openers: tuple[str, ...] = ()) -> list[Flag]:
    out: list[Flag] = []
    t = text.strip()
    if not t:
        return out
    if CANNED_OPENERS.search(t):
        out.append(Flag("canned_opener"))
    if AI_DISCLAIMER.search(t):
        out.append(Flag("ai_disclaimer"))
    if HUMAN_EXPERIENCE.search(t):
        out.append(Flag("human_experience_claim"))
    if FORCED_LAUGH.search(t) and not laughter_allowed:
        out.append(Flag("unprompted_laughter"))
    if FORCED_LAUGH.search(t) and re.search(r"(ha ?){5,}", t, re.I):
        out.append(Flag("exaggerated_laughter"))
    if len(ORDINAL_LIST.findall(t)) >= 2:
        out.append(Flag("spoken_list"))
    if STAGE_DIRECTION.search(t):
        out.append(Flag("stage_direction"))
    words = len(t.split())
    if verbosity in VERBOSITY and words > MAX_WORDS[verbosity]:
        out.append(Flag("overlong", f"{words}>{MAX_WORDS[verbosity]}"))
    if question and restates_question(t, question):
        out.append(Flag("restates_question"))
    if recent_openers and _opener(t) and _opener(t) in recent_openers:
        out.append(Flag("repeated_opener", _opener(t)))
    return out


def opener_of(text: str) -> str:
    return _opener(text)
