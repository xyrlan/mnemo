"""`mnemo regen-graph-edges` — refresh the trailing ``## Sources`` section
on every shared/{feedback,user,reference}/*.md so Obsidian (and any other
markdown graph viewer) renders rule↔briefing edges.

Idempotent: if a rule already has the section, it's stripped and rewritten
from the current ``sources:`` frontmatter. Body content above the
``GRAPH_SECTION_MARKER`` is never touched.

Pure rendering, no LLM calls, no schema change. The section is bookended
by an HTML comment marker so retrieval paths
(:func:`mnemo.core.text_utils.body_preview` and the reflex tokenizer)
strip it before scoring — zero impact on BM25F or what Claude sees.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.parser import command
from mnemo.core.text_utils import GRAPH_SECTION_MARKER


_RETRIEVAL_TYPES: tuple[str, ...] = ("feedback", "user", "reference")


def _build_section(sources: list[str]) -> str:
    if not sources:
        return ""
    wikilinks = "\n".join(
        f"- [[{s[:-3] if s.endswith('.md') else s}]]"
        for s in sources
    )
    return (
        f"\n{GRAPH_SECTION_MARKER}\n"
        f"## Sources\n"
        f"{wikilinks}\n"
    )


def _refresh_one(md: Path) -> bool:
    """Rewrite the file with an up-to-date Sources section. Returns True
    when the file content changed."""
    from mnemo.core.filters import parse_frontmatter

    text = md.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    sources_raw = fm.get("sources") or []
    if isinstance(sources_raw, str):
        sources_raw = [sources_raw]
    sources = [s for s in sources_raw if isinstance(s, str)]

    marker_idx = text.find(GRAPH_SECTION_MARKER)
    if marker_idx == -1:
        head = text.rstrip("\n") + "\n"
    else:
        head = text[:marker_idx].rstrip("\n") + "\n"

    new_text = head + _build_section(sources)
    if new_text == text:
        return False
    md.write_text(new_text, encoding="utf-8")
    return True


@command("regen-graph-edges")
def cmd_regen_graph_edges(args: argparse.Namespace) -> int:
    from mnemo import cli

    vault = cli._resolve_vault()
    changed = 0
    scanned = 0
    for type_dir in _RETRIEVAL_TYPES:
        d = vault / "shared" / type_dir
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            scanned += 1
            try:
                if _refresh_one(md):
                    changed += 1
            except (OSError, UnicodeDecodeError):
                continue
    print(f"scanned {scanned} rule(s); refreshed {changed} graph section(s)")
    return 0
