"""Shared utilities for forge modules."""

from __future__ import annotations

from pathlib import Path


def unique_path(path: Path) -> Path:
    """Return path with a -N suffix if it already exists."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    n = 2
    while True:
        candidate = parent / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1
