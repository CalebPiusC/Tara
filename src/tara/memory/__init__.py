"""Tara persistent memory: long-term records, explicit writes, bounded retrieval.

Live state (who may speak, interruptions, playback) stays in `tara.voice`; this
package never decides participation. Memory tools are granted in code and a write
requires an explicit participant request.
"""
from __future__ import annotations

from .intent import MemoryWriteGate, is_forget_request, is_recall_request, is_remember_request
from .models import (
    MAX_MEMORY_CHARS,
    MEMORY_KINDS,
    MEMORY_STATUSES,
    MemoryHit,
    MemoryItem,
    MemoryScope,
    normalize_text,
    utcnow,
)
from .service import DEFAULT_MAX_CONTEXT_CHARS, MemoryService, render_memories
from .store import InMemoryMemoryStore, MemoryStore, PostgresMemoryStore
from .tools import forget_memory_tool, recall_memory_tool, remember_memory_tool

__all__ = [
    "DEFAULT_MAX_CONTEXT_CHARS", "InMemoryMemoryStore", "MAX_MEMORY_CHARS", "MEMORY_KINDS", "MEMORY_STATUSES",
    "MemoryHit", "MemoryItem", "MemoryScope", "MemoryService", "MemoryStore", "MemoryWriteGate",
    "PostgresMemoryStore", "forget_memory_tool", "is_forget_request", "is_recall_request", "is_remember_request",
    "normalize_text", "recall_memory_tool", "remember_memory_tool", "render_memories", "utcnow",
]
