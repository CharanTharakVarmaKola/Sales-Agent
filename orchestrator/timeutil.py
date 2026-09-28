"""Shared ISO-8601 UTC timestamps (Phase B, Batch B2).

Implements carried MINOR 2 from B1: single source of truth for timestamps.
Store and the inference layer both consume this; no scattered
datetime.now(timezone.utc).isoformat() variants elsewhere.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current timezone-aware UTC datetime (for arithmetic). Pure, no I/O."""
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    """Current UTC time as ISO-8601 string. Pure, no I/O."""
    return utcnow().isoformat()
