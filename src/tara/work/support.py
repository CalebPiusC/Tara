"""Formatting helpers shared by work-session tools."""

from __future__ import annotations

from typing import Any


def with_owner(item: Any) -> str:
    """Format a stored fact with its owner, deadline, and confirmation state."""
    metadata = item.metadata or {}
    details = [f"{key}: {metadata[key]}" for key in ("owner", "deadline") if metadata.get(key)]
    text = item.text + (f" ({'; '.join(details)})" if details else "")
    return text if item.status == "confirmed" else f"{text} (unconfirmed)"