"""Phase-1 web search stub (the live tool is `tara.tools.utility.search`). No implementation ships; never invent results."""
from __future__ import annotations

from typing import Protocol

from ..core.contracts import Passage
from ..core.errors import ProviderError
from ..tools.executor import ToolDef


class WebSearch(Protocol):
    async def search(self, query: str, k: int = 3) -> list[Passage]: ...


class NotConfiguredSearch:
    async def search(self, query: str, k: int = 3) -> list[Passage]:
        raise ProviderError("web search is not configured")


def web_search_tool(search: WebSearch, timeout_s: float = 10.0) -> ToolDef:
    async def handler(query: str) -> list[dict[str, str]]:
        return [{"id": p.id, "text": p.text, "source": p.source} for p in await search.search(query, 3)]
    return ToolDef("web_search", {"query": str}, handler, description="Search the web for current information.",
                   timeout_s=timeout_s, permission="web_search", side_effect=False)
