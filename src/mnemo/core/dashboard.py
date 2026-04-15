"""HOME.md dashboard generator (v0.4).

Scans ``shared/<type>/*.md``, applies the shared filter, and renders a managed
block inside ``HOME.md`` at vault root. The block is delimited by HTML comment
markers so the rest of HOME.md (user-authored content) stays untouched across
regenerations.

Design decisions (2026-04-14):
- The dashboard replaces the old ``wiki/index.md`` + ``wiki/sources/`` duplication.
- The block lives at the TOP of HOME.md (after frontmatter if present) so the
  user's first view when opening Obsidian is the live project brain.
- Pages are grouped by trust tier (cross-agent synthesized first, auto-promoted
  direct reformats second) AND by topic tag.
- Wikilinks are path-qualified (``[[shared/<type>/<slug>]]``) to avoid slug
  ambiguity across types.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from mnemo.core import paths
from mnemo.core.filters import is_consumer_visible, parse_frontmatter, topic_tags

BLOCK_BEGIN = "<!-- mnemo:dashboard:begin -->"
BLOCK_END = "<!-- mnemo:dashboard:end -->"

_PAGE_TYPES = ("feedback", "user", "reference", "project")


@dataclass
class _Entry:
    slug: str
    type: str
    name: str
    source_count: int
    topic_tags: list[str]

    @property
    def wikilink(self) -> str:
        return f"[[shared/{self.type}/{self.slug}]]"


def _scan_shared(vault_root: Path) -> list[_Entry]:
    entries: list[_Entry] = []
    shared = vault_root / "shared"
    if not shared.is_dir():
        return entries
    for page_type in _PAGE_TYPES:
        type_dir = shared / page_type
        if not type_dir.is_dir():
            continue
        for md in sorted(type_dir.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = parse_frontmatter(text)
            if not is_consumer_visible(md, fm, vault_root):
                continue
            sources = fm.get("sources") or []
            if not isinstance(sources, list):
                sources = []
            name = str(fm.get("name") or md.stem)
            entries.append(_Entry(
                slug=md.stem,
                type=page_type,
                name=name,
                source_count=len(sources),
                topic_tags=topic_tags(fm),
            ))
    return entries


def _format_entry_line(e: _Entry) -> str:
    src_word = "source" if e.source_count == 1 else "sources"
    tags_part = ""
    if e.topic_tags:
        tags_part = " · " + " ".join(f"#{t}" for t in e.topic_tags)
    return f"- {e.wikilink} — {e.source_count} {src_word}{tags_part}"


def _by_trust_tier(entries: list[_Entry]) -> tuple[list[_Entry], list[_Entry]]:
    multi = [e for e in entries if e.source_count >= 2]
    single = [e for e in entries if e.source_count < 2]
    multi.sort(key=lambda e: (-e.source_count, e.name.lower()))
    single.sort(key=lambda e: (-e.source_count, e.name.lower()))
    return multi, single


def _by_topic(entries: list[_Entry]) -> list[tuple[str, list[_Entry]]]:
    buckets: dict[str, list[_Entry]] = {}
    for e in entries:
        for tag in e.topic_tags:
            buckets.setdefault(tag, []).append(e)
    for bucket in buckets.values():
        bucket.sort(key=lambda e: (-e.source_count, e.name.lower()))
    return sorted(buckets.items(), key=lambda kv: kv[0])


def _render_block_body(entries: list[_Entry]) -> str:
    lines: list[str] = [
        "## 🧠 Project brain (auto-generated — edits inside this block will be overwritten)",
        "",
        f"_Last updated: {datetime.now().isoformat(timespec='seconds')}_",
        "",
    ]
    if not entries:
        lines.append("_No consumer-visible pages yet. Run `mnemo extract` or wait "
                     "for the background auto-brain to populate ``shared/``._")
        return "\n".join(lines)

    multi, single = _by_trust_tier(entries)

    if multi:
        lines.append("### Cross-agent synthesized rules (high-trust)")
        for e in multi:
            lines.append(_format_entry_line(e))
        lines.append("")

    if single:
        lines.append("### Auto-promoted direct reformats")
        for e in single:
            lines.append(_format_entry_line(e))
        lines.append("")

    topics = _by_topic(entries)
    if topics:
        lines.append("### By topic")
        for tag, bucket in topics:
            n = len(bucket)
            word = "rule" if n == 1 else "rules"
            lines.append(f"#### #{tag} ({n} {word})")
            for e in bucket:
                src_word = "source" if e.source_count == 1 else "sources"
                lines.append(f"- {e.wikilink} — {e.source_count} {src_word}")
            lines.append("")

    # Trim trailing blank line
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _wrap_block(body: str) -> str:
    return f"{BLOCK_BEGIN}\n{body}\n{BLOCK_END}"


_DEFAULT_HOME_HEADER = "---\ntags: [home, dashboard]\n---\n# 🧠 Welcome to your mnemo vault\n\n"


def _upsert_block(home_text: str, new_block: str) -> str:
    """Insert or replace the managed block in an existing HOME.md text."""
    begin_idx = home_text.find(BLOCK_BEGIN)
    end_idx = home_text.find(BLOCK_END)
    if begin_idx != -1 and end_idx != -1 and end_idx > begin_idx:
        end_of_end = end_idx + len(BLOCK_END)
        return home_text[:begin_idx] + new_block + home_text[end_of_end:]
    # No block yet — insert at the top, after YAML frontmatter if present.
    insert_at = 0
    if home_text.startswith("---\n"):
        fm_close = home_text.find("\n---\n", 4)
        if fm_close != -1:
            insert_at = fm_close + len("\n---\n")
    prefix = home_text[:insert_at]
    suffix = home_text[insert_at:]
    separator_before = "" if prefix.endswith("\n") or prefix == "" else "\n"
    separator_after = "" if suffix.startswith("\n") else "\n"
    return f"{prefix}{separator_before}{new_block}\n{separator_after}{suffix}"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content.encode("utf-8"))
    os.replace(tmp, path)


def update_home_md(cfg: dict[str, Any]) -> Path:
    """Regenerate the managed dashboard block inside ``<vault_root>/HOME.md``.

    Safe to call after every extraction run — it only rewrites the delimited
    block and preserves any user-authored content above/below.
    """
    vault_root = paths.vault_root(cfg)
    entries = _scan_shared(vault_root)
    body = _render_block_body(entries)
    new_block = _wrap_block(body)

    home_path = vault_root / "HOME.md"
    if home_path.exists():
        existing = home_path.read_text(encoding="utf-8")
    else:
        existing = _DEFAULT_HOME_HEADER
    updated = _upsert_block(existing, new_block)
    _atomic_write(home_path, updated)
    return home_path
