"""Orchestration tools and bounded research for the Live voice path.

The Live model chooses among tools that application code has registered.  It never
owns permissions, arguments beyond the declared schema, task status, or source
provenance.  All external content is untrusted data.
"""
from __future__ import annotations

from .service import Orchestration

__all__ = ["Orchestration"]
