"""Tara persistent memory: long-term records, explicit writes, bounded retrieval.

Live state (who may speak, interruptions, playback) stays in `tara.voice`; this
package never decides participation. Memory tools are granted in code and a write
requires an explicit participant request.
"""
from __future__ import annotations

from .claims import Claim, classify_claim, durable_kind, is_tentative
from .intent import (
    MemoryWriteGate,
    is_confirmation,
    is_delete_meeting_request,
    is_forget_request,
    is_recall_request,
    is_remember_request,
    is_update_request,
)
from .kinds import NOTE_KINDS, RECORD_KINDS, normalize_kind, to_note_kind, to_record_kind
from .knowledge import (
    DEFAULT_CONTEXT_CHARS,
    DEFAULT_SEARCH_LIMIT,
    ChunkDraft,
    DocumentDraft,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeHit,
    KnowledgeService,
    KnowledgeStore,
    chunk_document,
)
from .knowledge import (
    rank_hits as rank_document_hits,
)
from .knowledge_store import InMemoryKnowledgeStore, PostgresKnowledgeStore
from .knowledge_tools import knowledge_tool_grants, search_knowledge_tool
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
from .retrieval import Query, RankedHit, claim_label, parse_query, rank_hits
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
    "ChunkDraft", "Claim", "DEFAULT_CONTEXT_CHARS", "DEFAULT_MAX_CONTEXT_CHARS", "DEFAULT_SEARCH_LIMIT",
    "DocumentDraft", "InMemoryKnowledgeStore", "InMemoryMemoryStore", "KnowledgeChunk", "KnowledgeDocument",
    "KnowledgeHit", "KnowledgeService", "KnowledgeStore", "MAX_MEMORY_CHARS", "MEMORY_KINDS", "MEMORY_STATUSES",
    "MeetingSession", "MemoryHit", "MemoryItem", "MemoryScope", "MemoryService", "MemoryStore", "MemoryWriteGate",
    "NOTE_KINDS", "PostgresKnowledgeStore", "PostgresMemoryStore", "Query", "RECORD_KINDS", "RankedHit",
    "chunk_document", "claim_label", "classify_claim", "delete_meeting_tool", "durable_kind", "forget_memory_tool",
    "is_confirmation", "is_delete_meeting_request", "is_forget_request", "is_recall_request", "is_remember_request",
    "is_tentative", "is_update_request", "knowledge_tool_grants", "list_meetings_tool", "meeting_lookup_tool",
    "meeting_slug", "memory_tool_grants", "normalize_kind", "normalize_text", "parse_query", "rank_document_hits",
    "rank_hits", "recall_memory_tool", "remember_memory_tool", "render_memories", "search_knowledge_tool", "to_note_kind",
    "to_record_kind", "update_memory_tool", "utcnow",
]
