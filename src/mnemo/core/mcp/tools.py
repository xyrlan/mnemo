"""Pure read functions exposed via the mnemo MCP server.

These are the only mnemo entry points Claude Code reaches when fulfilling an
``tools/call`` MCP request. Three load-bearing invariants:

1. They MUST apply :func:`mnemo.core.filters.is_consumer_visible` so machine
   view stays in lockstep with the human HOME dashboard.
2. They scan only the page types in ``_RETRIEVAL_TYPES`` — project pages are
   excluded by design (no topic tags by construction; the source files are
   already in Claude's auto-memory).
3. They return plain dicts/lists serializable by ``json.dumps`` so the server
   layer doesn't need any encoder glue.
"""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from mnemo.core.agent import resolve_agent
from mnemo.core.filters import (
    collect_existing_tags,
    is_consumer_visible,
    parse_frontmatter,
    topic_tags,
)

# Tier 2 page types eligible for MCP retrieval. Project pages are excluded:
# they have no topic tags by construction (promote.py is 1:1, no LLM), and
# their source files are already loaded by Claude's native auto-memory.
_RETRIEVAL_TYPES: tuple[str, ...] = ("feedback", "user", "reference")


class RuleRef(TypedDict):
    slug: str
    type: str
    source_count: int


class RuleBody(TypedDict):
    slug: str
    type: str
    name: str
    tags: list[str]
    sources: list[str]
    body: str


def _extract_body(text: str) -> str:
    """Strip the leading frontmatter from a mnemo-rendered page."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + len("\n---\n"):].lstrip("\n")


def _rule_belongs_to_project(fm: dict, project: str) -> bool:
    """True if any source path starts with ``bots/<project>/``."""
    prefix = f"bots/{project}/"
    return any(s.startswith(prefix) for s in (fm.get("sources") or []))


def _resolve_current_project(vault_root: Path) -> str | None:
    """Derive current project from cwd. Returns ``None`` on any failure."""
    try:
        return resolve_agent(str(Path.cwd())).name
    except Exception:
        return None


def list_rules_by_topic(vault_root: Path, topic: str) -> list[RuleRef]:
    """Return slugs whose topic tags include ``topic``.

    Sorted by source_count desc, then slug asc — multi-agent synthesized rules
    surface first because they represent stronger trust signal.
    """
    matches: list[RuleRef] = []
    for page_type in _RETRIEVAL_TYPES:
        type_dir = vault_root / "shared" / page_type
        if not type_dir.is_dir():
            continue
        for md in type_dir.glob("*.md"):
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = parse_frontmatter(text)
            if not is_consumer_visible(md, fm, vault_root):
                continue
            if topic not in topic_tags(fm):
                continue
            sources = fm.get("sources") or []
            matches.append({
                "slug": md.stem,
                "type": page_type,
                "source_count": len(sources),
            })
    matches.sort(key=lambda r: (-r["source_count"], r["slug"]))
    return matches


def read_mnemo_rule(vault_root: Path, slug: str) -> RuleBody | None:
    """Read a single rule by slug. Returns ``None`` for unknown / filtered slugs."""
    for page_type in _RETRIEVAL_TYPES:
        candidate = vault_root / "shared" / page_type / f"{slug}.md"
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            return None
        fm = parse_frontmatter(text)
        if not is_consumer_visible(candidate, fm, vault_root):
            return None
        return {
            "slug": slug,
            "type": page_type,
            "name": fm.get("name", slug),
            "tags": topic_tags(fm),
            "sources": fm.get("sources") or [],
            "body": _extract_body(text),
        }
    return None


def get_mnemo_topics(vault_root: Path) -> list[str]:
    """Return the sorted union of topic tags across every retrieval-eligible type."""
    seen: set[str] = set()
    for page_type in _RETRIEVAL_TYPES:
        seen.update(collect_existing_tags(vault_root, page_type))
    return sorted(seen)
