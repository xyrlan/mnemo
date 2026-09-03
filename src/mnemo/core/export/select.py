"""Which rule pages an export carries, and in what order.

The selection is the reflex's project scope — pages attributed to the
current project plus universal ones — restricted to the types a user can
act on (``feedback``, ``user`` by default). ``_inbox``, ``_archive`` and
``stability: evolving`` pages never qualify: the same visibility rule the
MCP tools apply (``filters.is_consumer_visible``).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from mnemo.core.filters import derive_rule_slug, is_consumer_visible, iter_shared_pages
from mnemo.core.reclassify_types import split_frontmatter
from mnemo.core.rule_activation import is_universal, projects_for_rule
from mnemo.core.text_utils import retrieval_body

DEFAULT_TYPES: tuple[str, ...] = ("feedback", "user")


@dataclass(frozen=True)
class ExportRule:
    slug: str
    name: str
    body: str
    quote: Optional[str]
    universal: bool
    source_count: int
    page_type: str


def select_rules(
    vault_root: Path,
    *,
    project: str,
    types: Sequence[str] = DEFAULT_TYPES,
    universal_threshold: int = 2,
    limit: Optional[int] = None,
) -> list[ExportRule]:
    """Rules scoped to *project*, universal first, then most-sourced, then slug."""
    vault_root = Path(vault_root)
    shared = vault_root / "shared"
    wanted = {types} if isinstance(types, str) else set(types)
    out: list[ExportRule] = []
    for md in iter_shared_pages(vault_root, include_inbox=False):
        rel = md.relative_to(shared).parts
        if len(rel) != 2 or rel[0] not in wanted:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = split_frontmatter(text)
        if not fm or not is_consumer_visible(md, fm, vault_root):
            continue
        sources = [s for s in (fm.get("sources") or []) if isinstance(s, str)]
        projects = projects_for_rule(sources, frontmatter=fm)
        universal = is_universal(projects, universal_threshold)
        if project not in projects and not universal:
            continue
        evidence = fm.get("evidence")
        quote = evidence.get("quote") if isinstance(evidence, dict) else None
        slug = derive_rule_slug(fm, md.stem)
        out.append(ExportRule(
            slug=slug,
            name=str(fm.get("name") or slug),
            body=retrieval_body(body).strip() + "\n",
            quote=str(quote).strip() if quote else None,
            universal=universal,
            source_count=len(sources),
            page_type=rel[0],
        ))
    out.sort(key=lambda r: (not r.universal, -r.source_count, r.slug))
    if limit is not None:
        out = out[: max(0, int(limit))]
    return out
