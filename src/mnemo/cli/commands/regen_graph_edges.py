"""`mnemo regen-graph-edges` — refresh the trailing graph-edges section on
shared rules AND on briefings, so Obsidian (and any other markdown graph
viewer) renders bi-directional rule↔briefing edges.

Two passes:

1. **Rules** (``shared/{feedback,user,reference}/*.md``) get a ``## Sources``
   section listing wikilinks to the briefings in their ``sources:``
   frontmatter — forward edges rule → briefing.
2. **Briefings** (``bots/<project>/briefings/sessions/*.md``) get a
   ``## Spawned rules`` section listing wikilinks to every rule whose
   ``sources:`` references the briefing — back edges briefing → rule. The
   inverse map is built from the rules pass; orphan briefings (mentioned by
   no rule) get their section pruned to empty.

Idempotent: existing sections are stripped and rewritten from the current
frontmatter / inverse map. Body content above the ``GRAPH_SECTION_MARKER``
is never touched.

Pure rendering, no LLM calls, no schema change. The section is bookended
by an HTML comment marker so retrieval paths and the briefing reader
(:func:`mnemo.core.text_utils.body_preview`, reflex tokenizer,
:func:`mnemo.core.extract.scanner._read_briefing_file`) strip it before
scoring or re-extraction — zero impact on BM25F, no spurious LLM re-runs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from mnemo.cli.parser import command
from mnemo.core.text_utils import GRAPH_SECTION_MARKER


_RETRIEVAL_TYPES: tuple[str, ...] = ("feedback", "user", "reference")


def _build_section(heading: str, wikilinks: list[str]) -> str:
    if not wikilinks:
        return ""
    body = "\n".join(f"- [[{w}]]" for w in wikilinks)
    return (
        f"\n{GRAPH_SECTION_MARKER}\n"
        f"## {heading}\n"
        f"{body}\n"
    )


def _wikilink_target(path_str: str) -> str:
    """Strip the trailing ``.md`` so Obsidian's wikilink resolver picks the
    file by its short name regardless of the user's link-format setting."""
    return path_str[:-3] if path_str.endswith(".md") else path_str


def _replace_or_append_section(text: str, section: str) -> str:
    """Drop everything from the existing marker on, then append *section*."""
    marker_idx = text.find(GRAPH_SECTION_MARKER)
    head = text[:marker_idx] if marker_idx != -1 else text
    head = head.rstrip("\n") + "\n"
    return head + section


def _refresh_rule(md: Path) -> bool:
    """Append/refresh a ``## Sources`` section on a rule .md file.
    Returns True when the file content changed."""
    from mnemo.core.filters import parse_frontmatter

    text = md.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    sources_raw = fm.get("sources") or []
    if isinstance(sources_raw, str):
        sources_raw = [sources_raw]
    sources = [s for s in sources_raw if isinstance(s, str)]
    section = _build_section(
        "Sources", [_wikilink_target(s) for s in sources]
    )
    new_text = _replace_or_append_section(text, section)
    if new_text == text:
        return False
    md.write_text(new_text, encoding="utf-8")
    return True


# Public for tests; aliases the rule-side refresher.
_refresh_one = _refresh_rule


def _refresh_briefing(briefing: Path, rule_stems: list[str]) -> bool:
    """Append/refresh a ``## Spawned rules`` section on a briefing .md file.

    *rule_stems* is the list of rule file stems (without ``.md``) that
    reference this briefing in their ``sources:`` frontmatter — these become
    wikilinks in the briefing's back-edges section.
    """
    text = briefing.read_text(encoding="utf-8")
    section = _build_section("Spawned rules", rule_stems)
    new_text = _replace_or_append_section(text, section)
    if new_text == text:
        return False
    briefing.write_text(new_text, encoding="utf-8")
    return True


def _build_briefing_to_rules_map(rules_dir_paths: list[Path]) -> dict[str, list[str]]:
    """Walk all rules and invert ``sources:`` → return a map from briefing
    relative path (e.g. ``bots/proj/briefings/sessions/abc.md``) to the
    sorted list of rule file stems that reference it."""
    from mnemo.core.filters import parse_frontmatter

    inverse: dict[str, list[str]] = defaultdict(list)
    for d in rules_dir_paths:
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            try:
                fm = parse_frontmatter(md.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            sources_raw = fm.get("sources") or []
            if isinstance(sources_raw, str):
                sources_raw = [sources_raw]
            for s in sources_raw:
                if isinstance(s, str) and s:
                    inverse[s].append(md.stem)
    for k in list(inverse.keys()):
        inverse[k] = sorted(set(inverse[k]))
    return dict(inverse)


@command("regen-graph-edges")
def cmd_regen_graph_edges(args: argparse.Namespace) -> int:
    from mnemo import cli

    vault = cli._resolve_vault()
    rule_dirs = [vault / "shared" / t for t in _RETRIEVAL_TYPES]

    # Pass 1: refresh rule files + build inverse map.
    rules_scanned = 0
    rules_refreshed = 0
    for d in rule_dirs:
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            rules_scanned += 1
            try:
                if _refresh_rule(md):
                    rules_refreshed += 1
            except (OSError, UnicodeDecodeError):
                continue

    inverse = _build_briefing_to_rules_map(rule_dirs)

    # Pass 2: refresh briefings (back-edges).
    briefings_scanned = 0
    briefings_refreshed = 0
    bots_dir = vault / "bots"
    if bots_dir.is_dir():
        for briefing in sorted(bots_dir.glob("*/briefings/sessions/*.md")):
            briefings_scanned += 1
            rel = briefing.relative_to(vault).as_posix()
            stems = inverse.get(rel, [])
            try:
                if _refresh_briefing(briefing, stems):
                    briefings_refreshed += 1
            except (OSError, UnicodeDecodeError):
                continue

    print(
        f"scanned {rules_scanned} rule(s) / {briefings_scanned} briefing(s); "
        f"refreshed {rules_refreshed} rule + {briefings_refreshed} briefing graph section(s)"
    )
    return 0
