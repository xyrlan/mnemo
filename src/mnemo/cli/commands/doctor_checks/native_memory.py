"""Doctor check — an auto-memory index longer than Claude Code loads (#573).

Claude Code loads only the first 200 lines or 25KB of a project's
``~/.claude/projects/<project>/memory/MEMORY.md`` at session start
(:mod:`mnemo.core.native_memory` has the source). When the index is longer it
tells the model, in a warning the person never sees: on 2026-09-29 a session
in this repo was told 46 of 246 lines were cut off. This check is where the
person sees it. Silent for every index Claude Code loads whole.

Cheap by design: one ``scandir`` of ``~/.claude/projects`` and one read per
``MEMORY.md``, no decoding of project directory names.
"""
from __future__ import annotations

from pathlib import Path


def _doctor_check_native_memory_cut(vault: Path) -> bool:
    """Doctor-registry adapter — True when silent, False on a warning."""
    from mnemo.core import native_memory

    try:
        cuts = native_memory.cut_indexes()
    except Exception:  # noqa: BLE001 — a diagnostic must not abort the run
        return True
    if not cuts:
        return True
    noun = "project has" if len(cuts) == 1 else "projects have"
    print(
        f"  ⚠ Auto-memory index cut: {len(cuts)} {noun} a MEMORY.md longer than "
        f"Claude Code loads at session start ({native_memory.LINE_LIMIT} lines or "
        f"{native_memory.CHAR_LIMIT:,} characters)"
    )
    for cut in cuts:
        entries = "entry" if cut["entries_past"] == 1 else "entries"
        print(
            f"    {cut['project']}: {cut['lines']} lines, {cut['chars']:,} characters "
            f"({cut['bytes']:,} bytes); loads {cut['loaded_lines']} lines, "
            f"{cut['entries_past']} index {entries} past the cut"
        )
    print(
        "       → sessions never see those entries unless Claude goes looking; ask "
        "Claude to shorten the index: one short line per entry, detail in topic "
        "files, stale entries merged or dropped"
    )
    return False
