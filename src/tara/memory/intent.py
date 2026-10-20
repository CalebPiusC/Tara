"""Deterministic English-only detection of explicit memory requests.

The model may *propose* a memory write, but the write handler checks this gate. A
normal statement is not a memory request, so the model cannot turn every utterance
into a permanent record. The gate is a permission check, not an intent classifier.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_REMEMBER = re.compile(
    r"\b(?:please\s+)?(?:remember|save|store|keep|(?:don'?t|do not) forget)\b[^.!?]{0,100}"
    r"\b(?:this|that|it|the|my|our|for next time|to memory|in memory)\b", re.I)
_FORGET = re.compile(
    r"\b(?:please\s+)?(?:forget|delete|remove)\b[^.!?]{0,100}"
    r"\b(?:this|that|it|the memory|what i told you|from memory)\b", re.I)
_RECALL = re.compile(
    r"\b(?:what did i (?:tell|say|mention)|do you remember|what do you remember|can you recall|"
    r"recall (?:that|this|what|the memory|your memory)|remind me)\b", re.I)
_FIRST_PERSON_REMEMBER = re.compile(r"\bi (?:really\s+)?remember\b", re.I)
_NEGATED_REMEMBER = re.compile(r"\b(?:i\s+)?(?:don'?t|do not|cannot|can'?t)\s+remember\b", re.I)
_QUESTION_REMEMBER = re.compile(r"\b(?:do|can|could|would)\s+you\s+remember\b", re.I)
_SAVE_HINT = re.compile(r"\b(?:for next time|to memory|in memory|save|store)\b", re.I)


def is_remember_request(text: str) -> bool:
    if _NEGATED_REMEMBER.search(text):
        return False
    if _QUESTION_REMEMBER.search(text) and not _SAVE_HINT.search(text):
        return False                                  # "Do you remember X?" is recall, not a save request
    if _FIRST_PERSON_REMEMBER.search(text) and not re.search(r"\b(?:please|can you|could you)\b", text, re.I):
        return False
    return bool(_REMEMBER.search(text))


def is_forget_request(text: str) -> bool:
    return bool(_FORGET.search(text))


def is_recall_request(text: str) -> bool:
    return bool(_RECALL.search(text))


@dataclass
class MemoryWriteGate:
    """Per-turn authorization set from the participant's final transcript."""

    remember: bool = False
    forget: bool = False

    def observe(self, text: str) -> None:
        self.remember = is_remember_request(text)
        self.forget = is_forget_request(text)

    def reset(self) -> None:
        self.remember = self.forget = False

    def remember_allowed(self) -> bool:
        return self.remember

    def forget_allowed(self) -> bool:
        return self.forget
