"""Doctor check — an auto-memory ``MEMORY.md`` longer than Claude Code loads (#573).

Claude Code loads only the first 200 lines or 25,000 characters of a
project's auto-memory index at session start (sources in
:mod:`mnemo.core.native_memory`). When an index is longer, Claude Code says
so to the model, never to the person: on 2026-09-29 a session in this repo was
told 46 of 246 lines were cut off, and nobody else saw it. This check is where
the person sees it. Silent while every index fits.
"""
from __future__ import annotations

import os
from pathlib import Path


def _tilde(path: Path) -> str:
    home = os.path.expanduser("~")
    text = str(path)
    if home and home != "~" and text.startswith(home.rstrip(os.sep) + os.sep):
        return "~" + text[len(home.rstrip(os.sep)):]
    return text


def _doctor_check_native_memory_cut(vault: Path) -> bool:
    """Doctor-registry adapter — True when silent, False on a warning."""
    from mnemo.core import native_memory as nm

    # `vault` is unused on purpose: the auto-memory lives under
    # ~/.claude/projects, not in the vault.
    cut = nm.over_limit()
    if not cut:
        return True
    print(
        f"  ⚠ auto-memory index: {len(cut)} project(s) have a {nm.INDEX} longer than "
        f"Claude Code loads at session start ({nm.LINE_LIMIT} lines or "
        f"{nm.CHAR_LIMIT:,} characters)"
    )
    for p in cut:
        print(
            f"    {_tilde(p['path'])}: {p['lines']:,} lines, {p['chars']:,} characters "
            f"({p['bytes']:,} bytes), over the {_limit(p['by'])} limit; loads "
            f"{p['loaded_lines']:,} lines, {p['entries_past']:,} of {p['entries']:,} "
            f"index entries past the cut"
        )
    print(
        "    → entries past the cut reach a session only if the model opens the "
        "file; keep one short line per entry, move detail into topic files, "
        "merge or drop stale entries"
    )
    return False


def _limit(by: str) -> str:
    from mnemo.core import native_memory as nm

    return f"{nm.LINE_LIMIT}-line" if by == "lines" else f"{nm.CHAR_LIMIT:,}-character"
