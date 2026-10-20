"""End-of-turn estimation. A pause is evidence, not proof (docs/VAD_AND_TURN_DETECTION_RESEARCH.md).

Required silence grows when: the last words look unfinished, the mode asks for
patience (interviews), or Tara just asked an open question. All base values
are parameters to calibrate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TRAILING_OPEN = re.compile(r"\b(and|but|or|so|because|um+|uh+|like|the|a|an|to|of|with|that|which|then|if|when)\s*[,.…]*$", re.IGNORECASE)
_FILLER_END = re.compile(r"(\.\.\.|…|,)\s*$")


def looks_complete(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if _TRAILING_OPEN.search(t) or _FILLER_END.search(t):
        return False
    return t[-1] in ".?!" or len(t.split()) >= 4


@dataclass(frozen=True)
class TurnParams:
    base_silence_ms: int = 700
    incomplete_multiplier: float = 2.0
    patience_multiplier: float = 1.0       # interviews set > 1
    open_question_multiplier: float = 1.5  # Tara just asked something open
    max_wait_ms: int = 6000


def required_silence_ms(text: str, p: TurnParams, after_open_question: bool = False) -> int:
    need = p.base_silence_ms * p.patience_multiplier
    if not looks_complete(text):
        need *= p.incomplete_multiplier
    if after_open_question:
        need *= p.open_question_multiplier
    return int(min(need, p.max_wait_ms))


def turn_ended(silence_ms: int, text: str, p: TurnParams, after_open_question: bool = False,
               other_started: bool = False) -> bool:
    """other_started: someone else began speaking, which closes the previous turn."""
    if other_started:
        return True
    return silence_ms >= required_silence_ms(text, p, after_open_question)
