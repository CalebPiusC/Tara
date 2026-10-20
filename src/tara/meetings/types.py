"""Meeting types: what kind of meeting this is changes how Tara behaves and what the minutes look like.

A type is chosen by the organizer (`--type`, `TARA_MEETING_TYPE` or by voice), never inferred from what people say. It
adds wording to the instructions, a minutes layout and a hint for the end-of-meeting summary. It never grants permission.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class MeetingType(str, Enum):
    GENERAL = "general"
    STANDUP = "standup"
    ONE_ON_ONE = "one_on_one"
    RETRO = "retro"
    CLIENT_CALL = "client_call"
    BRAINSTORM = "brainstorm"


@dataclass(frozen=True)
class TypeRules:
    label: str
    behaviour: str            # added to the live instructions; descriptors, not scripted lines
    minutes: str              # section layout for the minutes document
    digest_hint: str          # what the end-of-meeting summary should pay attention to
    kind: str                 # behaviour MeetingKind override ("" = let it be inferred)


RULES: dict[MeetingType, TypeRules] = {
    MeetingType.GENERAL: TypeRules(
        "general meeting", "This is a general meeting. Follow the conversation, note decisions and actions, stay quiet unless addressed.",
        "Sections: Meeting details, Summary, Decisions, Action items (table: Owner, Action, Deadline), Open questions.",
        "decisions, action items with owners, and open questions", ""),
    MeetingType.STANDUP: TypeRules(
        "stand-up",
        "This is a stand-up. Keep it brisk. For each person, listen for what they did, what they will do and what blocks "
        "them. Flag blockers immediately and keep a list of them. If someone talks longer than about two minutes, "
        "politely suggest taking it offline and park it.",
        "Sections: Meeting details, Per person (Done, Today, Blockers), Blockers to resolve (table: Owner, Blocker), Parked items.",
        "per person: what was done, what is planned, and blockers; list every blocker as an action", "technical"),
    MeetingType.ONE_ON_ONE: TypeRules(
        "one-to-one",
        "This is a one-to-one. Be warm and attentive and let the person lead. Listen for goals, feedback, concerns and "
        "commitments. Keep sensitive personal matters out of the notes unless the person asks you to record them.",
        "Sections: Meeting details, Topics discussed, Feedback given and received, Goals and commitments (table: Owner, Commitment, "
        "Deadline), Follow-ups for next time.",
        "goals, feedback, concerns and commitments by each person; leave out private personal matters", "casual"),
    MeetingType.RETRO: TypeRules(
        "retrospective",
        "This is a retrospective. Guide it into three parts: what went well, what did not, and what to change. Capture "
        "each point under the right heading, never assign blame, and finish by turning the changes into action items "
        "with owners.",
        "Sections: Meeting details, What went well, What did not go well, What we will change (table: Owner, Action, Deadline).",
        "what went well, what did not, and the changes agreed as actions with owners", "casual"),
    MeetingType.CLIENT_CALL: TypeRules(
        "client call",
        "This is a client call. Be polished and precise. Capture requirements, questions, objections and commitments word "
        "for word. Never commit to prices, dates or features yourself; say the team will confirm. End by restating next steps.",
        "Sections: Meeting details, Client requirements, Questions and objections, Commitments made (table: Who, Commitment, Date), "
        "Next steps.",
        "client requirements, objections, every commitment with who made it, and next steps", "client"),
    MeetingType.BRAINSTORM: TypeRules(
        "brainstorm",
        "This is a brainstorm. Capture every idea without judging it, label your own suggestions clearly as ideas, and "
        "when asked, group the ideas into themes and point out the strongest few.",
        "Sections: Meeting details, Ideas (grouped by theme), Favourites and why, Open questions, Next steps.",
        "every idea raised, grouped by theme, and which ones the group favoured", "brainstorm"),
}

_ALIASES = {
    "1:1": MeetingType.ONE_ON_ONE, "11": MeetingType.ONE_ON_ONE, "one on one": MeetingType.ONE_ON_ONE,
    "one-on-one": MeetingType.ONE_ON_ONE, "oneonone": MeetingType.ONE_ON_ONE, "one to one": MeetingType.ONE_ON_ONE,
    "stand-up": MeetingType.STANDUP, "stand up": MeetingType.STANDUP, "daily": MeetingType.STANDUP, "scrum": MeetingType.STANDUP,
    "retrospective": MeetingType.RETRO, "client": MeetingType.CLIENT_CALL, "client call": MeetingType.CLIENT_CALL,
    "customer call": MeetingType.CLIENT_CALL, "sales call": MeetingType.CLIENT_CALL, "ideas": MeetingType.BRAINSTORM,
    "brain storm": MeetingType.BRAINSTORM, "normal": MeetingType.GENERAL, "meeting": MeetingType.GENERAL,
}


def parse_type(value: str) -> MeetingType:
    """Forgiving about wording ('1:1', 'stand up', 'Client call'); raises ValueError for anything unknown."""
    v = re.sub(r"\s+", " ", (value or "").strip().lower().replace("_", " "))
    if not v:
        return MeetingType.GENERAL
    for t in MeetingType:
        if v == t.value.replace("_", " "):
            return t
    if v in _ALIASES:
        return _ALIASES[v]
    raise ValueError(f"unknown meeting type {value!r}; choose from {[t.value for t in MeetingType]}")
