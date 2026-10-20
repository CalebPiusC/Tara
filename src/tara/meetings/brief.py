"""The opening brief: what Tara says unprompted when a meeting of a continuing section starts."""
from __future__ import annotations

from ..memory.models import MeetingSession, MemoryScope
from ..memory.service import MemoryService, when
from .items import OpenItems


async def opening_brief(service: MemoryService, scope: MemoryScope, current: MeetingSession, items: OpenItems) -> str:
    """Instruction for Tara's first words, or "" when there is nothing worth saying (a brand new section, no open items)."""
    previous = [m for m in await service.sessions(scope) if m.number != current.number]
    if not previous:
        return ""
    open_items = await items.describe(await items.open_items())
    last = max(previous, key=lambda m: m.number)
    summary = next((i.text for i in reversed(await service.session_items(scope, last.session_id, 120)) if i.kind == "summary"), "")
    if not open_items and not summary:
        return ""
    actions = [r for r in open_items if r["type"] == "action"]
    parked = [r for r in open_items if r["type"] == "parked"]

    def line(r: dict[str, str]) -> str:
        extra = ", ".join(f"{k} {r[k]}" for k in ("owner", "deadline") if k in r)
        return r["text"][:140] + (f" ({extra})" if extra else "")

    parts = [f"The meeting has just started (meeting {current.number} of this section). Greet briefly in one short sentence, then "
             f"tell the participant, in at most two or three sentences, where things were left at meeting {last.number} "
             f"({when(last.started_at)})."]
    if summary:
        parts.append("Last time: " + summary[:300])
    if actions:
        parts.append(f"{len(actions)} action item{'s are' if len(actions) != 1 else ' is'} still open: "
                     + "; ".join(line(r) for r in actions[:4]) + ("; and more" if len(actions) > 4 else "") + ".")
    if parked:
        parts.append(f"{len(parked)} parked topic{'s' if len(parked) != 1 else ''}: " + "; ".join(line(r) for r in parked[:3]) + ".")
    parts.append("Offer to go through them, and do not read long lists. Do not mention anything not listed here.")
    return " ".join(parts)
