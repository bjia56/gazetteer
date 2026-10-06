"""Small helpers."""

from __future__ import annotations

LIMIT = 10


def clamp(value: int) -> int:
    """Keep a value under LIMIT."""
    return min(value, LIMIT)


def _unused_helper() -> None:
    return None
