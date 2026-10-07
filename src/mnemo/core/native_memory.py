"""How much of a project's auto-memory index Claude Code loads (#573, #574).

Claude Code keeps a project's auto-memory in
``~/.claude/projects/<project>/memory/``: an index, ``MEMORY.md``, and one
topic file per memory. Only the index's beginning loads at session start.

Sources, read 2026-10-07:

- code.claude.com/docs/en/memory: "The first 200 lines of ``MEMORY.md``, or
  the first 25KB, whichever comes first, are loaded at the start of every
  conversation. Content beyond that threshold is not loaded at session start."
- code.claude.com/docs/en/errors, "Memory index is over its read limit":
  "Only the content that loads counts toward the limits. YAML frontmatter and
  block-level HTML comments are stripped before the index is loaded, so
  they're excluded from the measurement" (since Claude Code v2.1.211).

The 25KB is 25,000 *characters*, cut at a whole line: of the loads Claude Code
recorded in this machine's transcripts, the 46 cut before line 201 held
24,789-24,990 characters each and up to 25,776 bytes, which no 25KB byte
limit allows (``tools/measure_native_memory_cut.py``, #574).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

LINE_LIMIT = 200
CHAR_LIMIT = 25_000
INDEX = "MEMORY.md"
_LINK = re.compile(r"\]\([^)\s]+\)")


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


def loadable_lines(text: str) -> List[str]:
    """An index's lines without what Claude Code strips before loading it:
    leading YAML frontmatter and block-level HTML comments."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for n in range(1, len(lines)):
            if lines[n].strip() == "---":
                lines = lines[n + 1:]
                break
    out: List[str] = []
    in_comment = False
    for line in lines:
        stripped = line.strip()
        if in_comment:
            in_comment = not stripped.endswith("-->")
            continue
        if stripped.startswith("<!--"):
            end = stripped.find("-->", 4)
            if end == -1:
                in_comment = True
                continue
            if end + 3 == len(stripped):
                continue
        out.append(line)
    return out


def index_cut(text: str) -> Dict[str, Any]:
    """An index as Claude Code measures it: lines, characters and bytes of
    what loads, the lines that fit, and the index entries (lines holding a
    link) past them."""
    lines = loadable_lines(text)
    joined = "\n".join(lines)
    cut = loaded_lines(joined)
    entries = [bool(_LINK.search(line)) for line in lines]
    return {
        "lines": len(lines),
        "chars": len(joined),
        "bytes": len(joined.encode("utf-8")),
        "loaded_lines": cut,
        "entries": sum(entries),
        "entries_past": sum(entries[cut:]),
        "by": "lines" if len(lines) > LINE_LIMIT and cut == LINE_LIMIT else "characters",
    }


def projects_root() -> Path:
    return Path(os.path.expanduser("~/.claude/projects"))


def over_limit(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every project whose ``memory/MEMORY.md`` is longer than Claude Code
    loads, longest first. One stat per project and a read only of the
    indexes that exist."""
    root = projects_root() if root is None else root
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    out = []
    for d in dirs:
        path = d / "memory" / INDEX
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        cut = index_cut(text)
        if cut["loaded_lines"] < cut["lines"]:
            out.append({"project": d.name, "path": path, **cut})
    out.sort(key=lambda p: (-p["entries_past"], -p["lines"], p["project"]))
    return out
