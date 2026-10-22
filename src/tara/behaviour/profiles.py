"""Behaviour profiles: they adjust style limits, they do not create different personalities
and they carry no permissions."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.config import INTERVIEW_MODES, MeetingConfig


class Profile(str, Enum):
    PROFESSIONAL = "professional"
    CONVERSATIONAL = "conversational"
    EXPRESSIVE = "expressive"


# Ordered scales (index = level) so limits can be compared and clamped
HUMOUR = ("none", "restrained", "light", "playful")
EXPRESSIVENESS = ("none", "subtle", "moderate", "animated")
VERBOSITY = ("brief", "concise", "detailed")
FORMALITY = ("formal", "neutral", "casual")


@dataclass(frozen=True)
class ProfileParams:
    max_humour: str
    max_expressiveness: str
    default_expressiveness: str
    default_verbosity: str
    default_formality: str
    default_tone: str
    humour_needs_playful_context: bool
    laughter_cooldown_s: float
    laughter_cooldown_turns: int
    style: tuple[str, ...]            # guidelines for the system instruction (descriptions, not scripted lines)


PROFILES: dict[Profile, ProfileParams] = {
    Profile.PROFESSIONAL: ProfileParams(
        max_humour="restrained", max_expressiveness="subtle", default_expressiveness="subtle",
        default_verbosity="concise", default_formality="neutral", default_tone="neutral",
        humour_needs_playful_context=True, laughter_cooldown_s=120.0, laughter_cooldown_turns=8,
        style=("Be clear, direct and relevant. Keep emotional expression subtle.",
               "Humour is restrained: never start jokes; a brief light reaction to someone else's joke is fine.",
               "Disagree respectfully and say why.")),
    Profile.CONVERSATIONAL: ProfileParams(
        max_humour="light", max_expressiveness="moderate", default_expressiveness="moderate",
        default_verbosity="concise", default_formality="neutral", default_tone="warm",
        humour_needs_playful_context=True, laughter_cooldown_s=60.0, laughter_cooldown_turns=5,
        style=("Sound relaxed and warm, like a colleague on a normal working day.",
               "Occasional light humour is fine when the room has set that tone; never at someone's expense.",
               "Use natural acknowledgements, and keep the substance in front.")),
    Profile.EXPRESSIVE: ProfileParams(
        max_humour="playful", max_expressiveness="animated", default_expressiveness="moderate",
        default_verbosity="concise", default_formality="casual", default_tone="warm",
        humour_needs_playful_context=True, laughter_cooldown_s=40.0, laughter_cooldown_turns=4,
        style=("Be more animated and varied in delivery, playful when the room is playful.",
               "Stay grounded: expressiveness never replaces accuracy, relevance or respect.",
               "Tone it down immediately when the mood turns serious.")),
}


def parse_profile(value: str | None, default: Profile = Profile.PROFESSIONAL) -> Profile:
    if not value:
        return default
    try:
        return Profile(value.strip().lower())
    except ValueError as e:
        raise ValueError(f"unknown behaviour profile {value!r}; choose from {[p.value for p in Profile]}") from e


def effective_profile(requested: Profile, cfg: MeetingConfig) -> Profile:
    """Interviews are always professional: humour or animation would distract and could bias evaluation."""
    return Profile.PROFESSIONAL if cfg.mode in INTERVIEW_MODES else requested


def clamp(level: str, scale: tuple[str, ...], ceiling: str) -> str:
    return scale[min(scale.index(level), scale.index(ceiling))]
