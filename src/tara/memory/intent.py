"""Deterministic English-only detection of explicit memory requests.

The model may *propose* a memory write, but the write handler checks this gate. A
normal statement is not a memory request, so the model cannot turn every utterance
into a permanent record. The gate is a permission check, not an intent classifier.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
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


_NOTE_REQUEST = re.compile(
    r"\b(?:please\s+)?(?:make a note(?: of| that)?|note (?:down|that)|write (?:this|that|it) down|"
    r"keep (?:this|that|it) in mind|take a note)\b", re.I)


_DELETE_MEETING = re.compile(
    r"\b(?:delete|clear|remove|erase|wipe|get rid of|discard|drop)\b[^.!?]{0,60}\bmeetings?\b", re.I)
_UPDATE = re.compile(
    r"\b(?:update|change|correct|fix|edit|amend|replace|revise)\b[^.!?]{0,80}"
    r"\b(?:memory|memories|that|it|this|the|my|our|what)\b|\b(?:that'?s|that is|it'?s|it is) (?:wrong|not right|incorrect|outdated)\b|"
    r"\bno,? (?:it'?s|it is|that'?s|that is|actually)\b", re.I)
_YES = re.compile(
    r"\b(?:yes|yeah|yep|yup|sure|confirm(?:ed)?|go ahead|do it|delete it|proceed|i'?m sure|i am sure|absolutely|definitely)\b", re.I)
_NO = re.compile(r"\b(?:no|nope|don'?t|do not|stop|cancel|wait|never ?mind|not sure|keep it)\b", re.I)


_ACTION = re.compile(
    r"\b(?:add|create|note|record|log|make|put|write)\b[^.!?]{0,40}\b(?:action(?: item)?s?|tasks?|to-?dos?)\b|\baction item\b|"
    r"\bassign(?:ed)?\b|\bremind (?:me|us)\b|\b(?:i|we)'?ll (?:need to|have to)\b|\bcan you (?:make sure|follow up)\b", re.I)
_PARK = re.compile(
    r"\bparking lot\b|\bpark (?:that|this|it)\b|\b(?:table|come back to|revisit|circle back to) (?:that|this|it)\b|"
    r"\blet'?s (?:talk|discuss) (?:about )?(?:that|this|it) later\b|\boff[- ]topic\b", re.I)
_COMPLETE = re.compile(
    r"\b(?:mark|tick|check|cross)\b[^.!?]{0,40}\b(?:done|complete[d]?|finished|off|resolved|closed)\b|"
    r"\b(?:that'?s|it'?s|that is|it is|is|are|was|were|has been|have been) (?:now )?(?:done|completed|finished|resolved|closed)\b|"
    r"\b(?:we|i|they|he|she) (?:have |has |already )?(?:finished|completed|did|done|resolved|closed)\b", re.I)


def is_action_request(text: str) -> bool:
    return bool(_ACTION.search(text))


def is_park_request(text: str) -> bool:
    return bool(_PARK.search(text))


def is_complete_request(text: str) -> bool:
    return bool(_COMPLETE.search(text))


def is_delete_meeting_request(text: str) -> bool:
    return bool(_DELETE_MEETING.search(text))


def is_update_request(text: str) -> bool:
    return bool(_UPDATE.search(text))


def is_confirmation(text: str) -> bool:
    """A clear yes: contains a yes word and no refusing word."""
    return bool(_YES.search(text)) and not _NO.search(text)


def is_remember_request(text: str) -> bool:
    if _NEGATED_REMEMBER.search(text):
        return False
    if _NOTE_REQUEST.search(text):
        return True
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
    """Authorization from the participant's final transcripts.

    A request stays valid for `window_s` seconds, because the input transcript often arrives after the model has
    already called the tool, and the model may need a second turn to do the save.
    """

    remember: bool = False
    forget: bool = False
    window_s: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _remember_until: float = 0.0
    _forget_until: float = 0.0
    _delete_meeting_until: float = 0.0
    _update_until: float = 0.0
    _yes_at: float = 0.0
    _action_until: float = 0.0
    _park_until: float = 0.0
    _complete_until: float = 0.0

    def observe(self, text: str) -> None:
        now = self.clock()
        if is_remember_request(text):
            self._remember_until = now + self.window_s
        if is_forget_request(text):
            self._forget_until = now + self.window_s
        if is_delete_meeting_request(text):
            self._delete_meeting_until = now + self.window_s
        if is_update_request(text):
            self._update_until = now + self.window_s
        if is_action_request(text):
            self._action_until = now + self.window_s
        if is_park_request(text):
            self._park_until = now + self.window_s
        if is_complete_request(text):
            self._complete_until = now + self.window_s
        if is_confirmation(text):
            self._yes_at = now
        elif _NO.search(text):
            self._yes_at = 0.0                      # a refusal after a yes cancels it
        self.remember = now < self._remember_until
        self.forget = now < self._forget_until

    def delete_meeting_allowed(self) -> bool:
        return self.clock() < self._delete_meeting_until

    def action_allowed(self) -> bool:
        return self.clock() < self._action_until

    def park_allowed(self) -> bool:
        return self.clock() < self._park_until

    def complete_allowed(self) -> bool:
        return self.clock() < self._complete_until

    def update_allowed(self) -> bool:
        return self.clock() < self._update_until

    def confirmed_since(self, t: float) -> bool:
        """True when the participant said a clear yes after time `t` (when the question was asked)."""
        return self._yes_at > t

    def reset(self) -> None:
        self.remember = self.forget = False
        self._remember_until = self._forget_until = self._delete_meeting_until = self._update_until = self._yes_at = 0.0
        self._action_until = self._park_until = self._complete_until = 0.0

    def remember_allowed(self) -> bool:
        return self.clock() < self._remember_until

    def forget_allowed(self) -> bool:
        return self.clock() < self._forget_until
