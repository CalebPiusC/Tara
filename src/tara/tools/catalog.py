"""Every tool the model can call in a live session, grouped, with where it is built and what switches it on.

This is a map, not a registry: tools are still built and granted by the code that owns their state (the composition root
in `voice/app.py` decides what a session gets). `tests/tools/test_catalog.py` checks the map against every `ToolDef` in the
source, so a new tool cannot be added without appearing here. The dormant `tara.legacy` engine is not listed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolGroup:
    key: str
    title: str
    module: str              # where the ToolDefs are built
    enabled_by: str          # the setting or condition that puts the group into a session
    tools: tuple[str, ...]


CATALOG: tuple[ToolGroup, ...] = (
    ToolGroup("time", "Time and dates", "tara.tools.utility.time", "TARA_TOOLS_ENABLED (default on)",
              ("get_time", "convert_time", "time_until")),
    ToolGroup("calculation", "Calculator and unit conversion", "tara.tools.utility.calculator", "TARA_TOOLS_ENABLED (default on)",
              ("calculate", "convert_units")),
    ToolGroup("location", "Places and countries", "tara.tools.utility.places", "TARA_TOOLS_ENABLED (default on)",
              ("geocode_place", "country_info", "get_time_at_place")),
    ToolGroup("weather", "Weather", "tara.tools.utility.weather", "TARA_TOOLS_ENABLED (default on)", ("get_weather",)),
    ToolGroup("search", "Web search", "tara.tools.utility.search", "TARA_TOOLS_ENABLED and a search provider key", ("web_search",)),
    ToolGroup("web", "Fetch a public page", "tara.tools.utility.fetch", "TARA_TOOLS_ENABLED (default on)", ("fetch_url",)),
    ToolGroup("research", "Bounded research", "tara.tools.utility.research", "TARA_TOOLS_ENABLED, research on, and a search provider",
              ("research", "start_research", "research_status", "research_result", "cancel_research")),
    ToolGroup("memory", "Long-term memory", "tara.memory.tools", "TARA_MEMORY_ENABLED",
              ("remember_memory", "recall_memory", "forget_memory", "update_memory", "delete_meeting", "list_meetings",
               "meeting_lookup")),
    ToolGroup("notes", "Meeting notes", "tara.voice.memory", "the spoken 'notes on' command is allowed (default)", ("record_note",)),
    ToolGroup("knowledge", "Approved company knowledge", "tara.memory.knowledge_tools",
              "TARA_MEMORY_ENABLED and TARA_KNOWLEDGE_ENABLED", ("search_knowledge",)),
    ToolGroup("secretary", "Meeting recall", "tara.secretary.recall", "TARA_WORK_ENABLED", ("recall_meeting", "meeting_recap")),
    ToolGroup("meeting", "Recap, meeting type, vocabulary, agenda", "tara.workflows", "TARA_WORK_ENABLED",
              ("recap_meeting", "set_meeting_type", "add_vocabulary", "set_agenda", "agenda_status", "agenda_mark")),
    ToolGroup("open_items", "Action items and parked topics", "tara.meetings.items", "TARA_WORK_ENABLED and TARA_MEMORY_ENABLED",
              ("add_action", "park_item", "list_open_items", "complete_item")),
    ToolGroup("documents", "Documents and emailed minutes", "tara.workflows",
              "TARA_WORK_ENABLED and TARA_DOCS_ENABLED (email_summary also needs TARA_EMAIL_*)",
              ("create_document", "job_status", "email_summary")),
    ToolGroup("candidates", "Candidate briefs", "tara.workflows", "TARA_WORK_ENABLED and TARA_HIRING_ENABLED",
              ("list_candidates", "get_candidate", "prepare_candidate")),
    ToolGroup("interview", "Live interview", "tara.workflows", "TARA_WORK_ENABLED and TARA_HIRING_ENABLED",
              ("start_interview", "interview_next_question", "interview_record_answer", "finish_interview")),
)


def all_tool_names() -> frozenset[str]:
    return frozenset(name for group in CATALOG for name in group.tools)


def group_of(tool_name: str) -> ToolGroup | None:
    return next((g for g in CATALOG if tool_name in g.tools), None)


def render() -> str:
    """The catalogue as plain text, for `tara tools`."""
    out: list[str] = []
    for g in CATALOG:
        out.append(f"{g.title} [{g.key}]  ({g.module})")
        out.append(f"  on when: {g.enabled_by}")
        out.extend(f"  - {name}" for name in g.tools)
        out.append("")
    out.append(f"{len(all_tool_names())} tools in {len(CATALOG)} groups.")
    return "\n".join(out)
