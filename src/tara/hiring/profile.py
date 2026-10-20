from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_ID = re.compile(r"[^a-z0-9]+")
# Protected or irrelevant personal attributes are removed BEFORE text reaches any model.
_PROTECTED_LINE = re.compile(
    r"(?im)^\s*(date of birth|d\.?o\.?b\.?|born( on| in)?|age|gender|sex|marital status|married|single|nationality|citizenship|"
    r"religion|ethnicity|race|photo|passport|national id|visa status|children|pregnan\w*|disabilit\w*)\b.*$")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")
MAX_RAW = 60_000


def slug(text: str) -> str:
    return _ID.sub("-", text.lower()).strip("-")[:40] or "candidate"


def redact_for_model(text: str) -> str:
    """Remove protected-attribute lines, emails and phone numbers. Heuristic: a CV can still contain such facts in prose;
    that residual risk is documented, and the interview rubric never scores them."""
    text = _PROTECTED_LINE.sub("", text)
    text = _EMAIL.sub("[email removed]", text)
    text = _PHONE.sub("[phone removed]", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@dataclass(frozen=True)
class SourceNote:
    kind: str                 # cv | portfolio | github
    ref: str                  # file name or URL (never a secret)
    chars: int = 0
    note: str = ""


@dataclass
class CandidateProfile:
    candidate_id: str
    label: str                                    # what the interviewer calls them; not used for scoring
    role_title: str = ""
    summary: str = ""
    skills: list[str] = field(default_factory=list)
    experience: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    sources: list[SourceNote] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_excerpt: str = ""                         # redacted text kept for question grounding, bounded

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> CandidateProfile:
        src = [SourceNote(**s) for s in d.get("sources", [])]
        base = {k: v for k, v in d.items() if k != "sources"}
        return CandidateProfile(**base, sources=src)

    def brief(self, max_chars: int = 2500) -> str:
        """What the interviewer model sees: no name, contact details or protected attributes."""
        lines = [f"Role applied for: {self.role_title or 'not stated'}"]
        if self.summary:
            lines.append(f"Summary: {self.summary}")
        if self.skills:
            lines.append("Skills claimed: " + ", ".join(self.skills[:30]))
        if self.experience:
            lines.append("Experience: " + " | ".join(self.experience[:8]))
        if self.projects:
            lines.append("Projects: " + " | ".join(f"{p.get('name', '')}: {str(p.get('description', ''))[:120]}"
                                                    f" [{', '.join(p.get('languages', [])[:4])}]" for p in self.projects[:8]))
        if self.education:
            lines.append("Education: " + " | ".join(self.education[:4]))
        if self.warnings:
            lines.append("Data quality notes: " + "; ".join(self.warnings[:5]))
        return "\n".join(lines)[:max_chars]
