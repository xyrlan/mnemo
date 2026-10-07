"""How much of Claude Code's auto-memory index it loads at session start (#573).

Claude Code keeps a project's auto-memory in
``~/.claude/projects/<project>/memory/``: an index, ``MEMORY.md``, and one
topic file per memory. Its docs (https://code.claude.com/docs/en/memory,
"How Claude remembers your project", read 2026-10-07): "The first 200 lines
of ``MEMORY.md``, or the first 25KB, whichever comes first, are loaded at the
start of every conversation. Content beyond that threshold is not loaded at
session start."

The 25KB is 25,000 *characters*, cut at a whole line, not 25,600 bytes:
``tools/measure_native_memory_cut.py`` (#574) reads the loads Claude Code
records in its transcripts, and the 46 cut before line 201 on 2026-10-07 held
24,789-24,990 characters each and up to 25,776 bytes, which no byte limit of
25KB allows.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Claude Code's load limits for ``MEMORY.md``: 200 lines or 25KB, whichever
#: comes first; the 25KB measured as characters (see the module docstring).
LINE_LIMIT = 200
CHAR_LIMIT = 25_000
INDEX = "MEMORY.md"


def loaded_lines(text: str, *, line_limit: int = LINE_LIMIT, char_limit: int = CHAR_LIMIT) -> int:
    """How many whole lines of an index Claude Code loads: the most lines,
    at most ``line_limit``, whose text joined by newlines is at most
    ``char_limit`` characters."""
    lines = text.splitlines()
    size = -1
    for n, line in enumerate(lines[:line_limit], 1):
        size += len(line) + 1
        if size > char_limit:
            return n - 1
    return min(len(lines), line_limit)


def _is_entry(line: str) -> bool:
    """An index entry: any non-blank line but a heading."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def index_cut(text: str) -> Optional[Dict[str, Any]]:
    """An index's size against the limits, or None when Claude Code loads
    all of it."""
    lines = text.splitlines()
    cut = loaded_lines(text)
    if cut >= len(lines):
        return None
    return {
        "lines": len(lines),
        "chars": len("\n".join(lines)),
        "bytes": len(text.encode("utf-8")),
        "loaded_lines": cut,
        "entries_past": sum(1 for line in lines[cut:] if _is_entry(line)),
    }


def projects_root() -> Path:
    return Path(os.path.expanduser("~/.claude/projects"))


def cut_indexes(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every project under ``root`` whose ``MEMORY.md`` Claude Code cuts,
    sorted by project directory name, each :func:`index_cut` plus
    ``project``. One ``scandir`` and one read per project."""
    root = projects_root() if root is None else root
    out: List[Dict[str, Any]] = []
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return out
    for entry in entries:
        try:
            with open(os.path.join(entry.path, "memory", INDEX), "rb") as fh:
                raw = fh.read()
        except OSError:  # no index, not a directory, unreadable
            continue
        cut = index_cut(raw.decode("utf-8", errors="replace"))
        if cut is not None:
            cut["bytes"] = len(raw)
            cut["project"] = entry.name
            out.append(cut)
    return out
