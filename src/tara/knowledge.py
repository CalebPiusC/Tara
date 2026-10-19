"""Approved-knowledge provider. Retrieval is keyword overlap (no embeddings, no
downloads); swap for a vector index later behind the same Protocol."""
from __future__ import annotations

import re

from .contracts import Passage

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an the is are of to and or in on for with do does it what how can you your we our i".split())


def _tokens(s: str) -> set[str]:
    return {w for w in _WORD.findall(s.lower()) if w not in _STOP and len(w) > 1}


class InMemoryKnowledge:
    def __init__(self, passages: list[Passage]):
        ids = [p.id for p in passages]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate passage ids")
        self._p = [(p, _tokens(p.text)) for p in passages]

    async def search(self, query: str, k: int = 3) -> list[Passage]:
        q = _tokens(query)
        scored = [(len(q & t), p) for p, t in self._p]
        scored = [(s, p) for s, p in scored if s > 0]
        scored.sort(key=lambda sp: (-sp[0], sp[1].id))
        return [p for _, p in scored[:k]]
