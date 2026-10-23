"""One canonical vocabulary for memory kinds.

Two vocabularies existed: persisted records used `action`, while live meeting notes used `action_item`, and
call sites compared across them (`kind not in ("decision", "action_item")` against record kinds). This module is
the single translation point. Persisted kinds are canonical; note kinds remain the spoken-notes vocabulary and
are mapped explicitly at the boundary rather than by scattered conditionals.
"""
from __future__ import annotations

# Persisted kinds (`memory_records.kind`, enforced by the database check constraint).
RECORD_KINDS = ("fact", "preference", "project", "decision", "correction", "note", "episode", "summary", "action")

# In-process meeting-note kinds (`voice.memory.MemoryRecord`).
NOTE_KINDS = ("decision", "action_item", "open_question", "proposal", "summary")
PROMOTABLE_NOTE_KINDS = ("decision", "action_item")

# A note kind that has no record equivalent is stored as a `note` with metadata["note_kind"].
NOTE_TO_RECORD = {
    "decision": "decision",
    "action_item": "action",
    "open_question": "note",
    "proposal": "note",
    "summary": "summary",
}
RECORD_TO_NOTE = {
    "decision": "decision",
    "action": "action_item",
    "summary": "summary",
    "note": "open_question",          # only when metadata says it came from an open question
}

# Accepted spellings from model output, tools and older records. Never a new stored kind.
ALIASES = {
    "action": "action",
    "action_item": "action",
    "action-item": "action",
    "action items": "action",
    "actions": "action",
    "open_question": "note",
    "open question": "note",
    "question": "note",
    "proposal": "note",
    "hypothetical": "note",
    "todo": "action",
    "to-do": "action",
    "commitment": "action",
    "task": "action",
    "fact": "fact",
    "preference": "preference",
    "project": "project",
    "decision": "decision",
    "correction": "correction",
    "note": "note",
    "episode": "episode",
    "transcript": "episode",
    "summary": "summary",
}

# Kinds that carry durable meaning (not raw transcript or generated summary).
DURABLE_KINDS = ("fact", "preference", "project", "decision", "correction", "note", "action")
TRANSCRIPT_KINDS = ("episode", "summary")
# Kinds an explicit correction may supersede.
CORRECTABLE_KINDS = DURABLE_KINDS


def normalize_kind(kind: str) -> str:
    """Map any accepted spelling to a canonical persisted kind. Raises ValueError when it is not one."""
    key = " ".join(str(kind).strip().lower().replace("-", "_").split())
    out = ALIASES.get(key) or ALIASES.get(key.replace("_", " "))
    if out is None:
        raise ValueError(f"unknown memory kind {kind!r}; choose from {list(RECORD_KINDS)}")
    return out


def to_record_kind(note_kind: str) -> str:
    """A meeting-note kind as the persisted kind that stores it."""
    key = " ".join(str(note_kind).strip().lower().replace("-", "_").split())
    if key in NOTE_TO_RECORD:
        return NOTE_TO_RECORD[key]
    return normalize_kind(key)


def to_note_kind(record_kind: str, *, note_kind: str = "") -> str:
    """A persisted kind back to the live-note vocabulary (used when reading durable notes)."""
    if note_kind in NOTE_KINDS:
        return note_kind
    key = normalize_kind(record_kind)
    return RECORD_TO_NOTE.get(key, "summary" if key == "summary" else "open_question")
