"""Shared log-rotation utility for JSONL files."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator


def rotated_path(log_path: Path) -> Path:
    """The ``.1`` path a rotation of ``log_path`` renames itself to."""
    return log_path.with_suffix(log_path.suffix + ".1")


def rotate_if_needed(log_path: Path, max_bytes: int) -> None:
    """Rotate log_path → log_path.1 if it exceeds max_bytes. Never raises."""
    try:
        if log_path.exists() and log_path.stat().st_size > max_bytes:
            os.replace(log_path, rotated_path(log_path))
    except OSError:
        pass


def _iter_file_rows(path: Path) -> Iterator[dict]:
    """Yield dict rows from a JSONL file, skipping anything unreadable."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue  # a torn line from a killed append
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def iter_rotated_rows(log_path: Path) -> Iterator[dict]:
    """Yield dict rows from ``log_path``'s rotated file, then the live one.

    ``rotate_if_needed`` renames ``log_path`` to ``log_path.1`` the moment it
    crosses its size cap, so on a busy vault a time window (the last 14 days,
    the last 10 decisions, a 180-day dead-rule cutoff) can straddle both
    files. Reading only the live file would silently truncate that window.
    Rows come out oldest first: the rotated file's rows, in file order, then
    the live file's rows, in file order. Any reader wanting newest-first
    should reverse the result itself.

    A missing file contributes no rows. An ``OSError`` on either file (e.g. a
    directory in its place) ends that file's contribution silently — this
    never raises. Blank lines, torn/invalid JSON, and non-dict rows are
    skipped.
    """
    yield from _iter_file_rows(rotated_path(log_path))
    yield from _iter_file_rows(log_path)
