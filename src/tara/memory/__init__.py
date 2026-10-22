"""Tara persistent memory: long-term records, explicit writes, bounded retrieval.

Live state (who may speak, interruptions, playback) stays in `tara.voice`; this
package never decides participation. Memory tools are granted in code and a write
requires an explicit participant request.
"""
from __future__ import annotations

from .intent import (
    MemoryWriteGate,
    is_confirmation,
    is_delete_meeting_request,
    is_forget_request,
    is_recall_request,
    is_remember_request,
    is_update_request,
)
from .models import (
    MAX_MEMORY_CHARS,
    MEMORY_KINDS,
    MEMORY_STATUSES,
    MeetingSession,
    MemoryHit,
    MemoryItem,
    MemoryScope,
    meeting_slug,
    normalize_text,
    utcnow,
)
from .service import DEFAULT_MAX_CONTEXT_CHARS, MemoryService, render_memories
from .store import InMemoryMemoryStore, MemoryStore, PostgresMemoryStore
from .tools import (
    delete_meeting_tool,
    forget_memory_tool,
    list_meetings_tool,
    meeting_lookup_tool,
    memory_tool_grants,
    recall_memory_tool,
    remember_memory_tool,
    update_memory_tool,
)

__all__ = [
    "DEFAULT_MAX_CONTEXT_CHARS", "InMemoryMemoryStore", "MAX_MEMORY_CHARS", "MEMORY_KINDS", "MEMORY_STATUSES",
    "MeetingSession", "MemoryHit", "is_confirmation", "is_delete_meeting_request", "is_update_request", "MemoryItem", "MemoryScope", "MemoryService", "MemoryStore", "MemoryWriteGate",
    "PostgresMemoryStore", "delete_meeting_tool", "update_memory_tool", "forget_memory_tool", "list_meetings_tool", "meeting_lookup_tool", "is_forget_request", "is_recall_request", "is_remember_request",
    "meeting_slug", "memory_tool_grants", "normalize_text", "recall_memory_tool", "remember_memory_tool", "render_memories", "utcnow",
]
