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
    derive_rule_slug,
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


def _rule_in_scope(rule: dict, project: str | None, scope: str) -> bool:
    """Apply v0.7 scope semantics to a rule entry from the unified index.

    - ``scope="project"`` (default): local to *project* OR universal.
    - ``scope="local-only"``: local to *project* only (legacy v0.6.2 behaviour).
    - ``scope="vault"``: always True.
    """
    if scope == "vault":
        return True
    projects: list[str] = rule.get("projects", []) or []
    is_local = project is not None and project in projects
    if scope == "local-only":
        return is_local
    # Default: "project"
    if is_local:
        return True
    return bool(rule.get("universal"))


def _resolve_current_project(vault_root: Path) -> str | None:
    """Derive current project from cwd. Returns ``None`` on any failure."""
    try:
        return resolve_agent(str(Path.cwd())).name
    except Exception:
        return None


def list_rules_by_topic(
    vault_root: Path,
    topic: str,
    *,
    scope: str = "project",
    project: str | None = None,
) -> list[RuleRef]:
    """Return slugs whose topic tags include ``topic``, filtered by scope.

    Reads from the unified rule-activation-index.json when available;
    falls back to a glob+parse walk of shared/{feedback,user,reference}/ when
    the index is missing. In fallback mode, every rule is treated as local
    (universality is only available when the index is built).

    Sorted by source_count desc, then slug asc.
    """
    from mnemo.core import rule_activation

    idx = rule_activation.load_index(vault_root)
    if idx is not None and "rules" in idx:
        matches: list[RuleRef] = []
        for slug, rule in idx["rules"].items():
            if topic not in rule.get("topic_tags", []):
                continue
            if not _rule_in_scope(rule, project, scope):
                continue
            matches.append({
                "slug": slug,
                "type": rule.get("type", "feedback"),
                "source_count": rule.get("source_count", 0),
            })
        matches.sort(key=lambda r: (-r["source_count"], r["slug"]))
        return matches

    # Fallback: legacy glob+parse. Universality unavailable; treat all as local.
    filter_project = scope in ("project", "local-only") and project is not None
    legacy: list[RuleRef] = []
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
            if filter_project and not _rule_belongs_to_project(fm, project):
                continue
            sources = fm.get("sources") or []
            slug = derive_rule_slug(fm, md.stem)
            legacy.append({
                "slug": slug,
                "type": page_type,
                "source_count": len(sources),
            })
    legacy.sort(key=lambda r: (-r["source_count"], r["slug"]))
    return legacy


def read_mnemo_rule(
    vault_root: Path,
    slug: str,
    *,
    scope: str = "project",
    project: str | None = None,
) -> RuleBody | None:
    """Read a single rule by slug. Returns ``None`` for unknown / filtered slugs."""
    from mnemo.core import rule_activation

    idx = rule_activation.load_index(vault_root)
    if idx is not None and "rules" in idx:
        rule = idx["rules"].get(slug)
        if rule is None:
            return None
        if not _rule_in_scope(rule, project, scope):
            return None
        # Still need the full body — read the file once, the index only has a preview.
        page_type = rule.get("type", "feedback")
        # The index key (slug) can be the human-readable `name`, which does NOT
        # match the file stem on disk. Prefer the stored `file_stem`; if missing
        # (stale pre-fix index), fall back to scanning the type dir and matching
        # the frontmatter `name`/`slug` against the requested slug.
        stem = rule.get("file_stem")
        text: str | None = None
        if stem:
            try:
                text = (vault_root / "shared" / page_type / f"{stem}.md").read_text(encoding="utf-8")
            except OSError:
                text = None
        if text is None:
            type_dir = vault_root / "shared" / page_type
            if type_dir.is_dir():
                for candidate in type_dir.glob("*.md"):
                    try:
                        probe = candidate.read_text(encoding="utf-8")
                    except OSError:
                        continue
                    fm = parse_frontmatter(probe)
                    derived = derive_rule_slug(fm, candidate.stem)
                    if derived == slug:
                        text = probe
                        break
        if text is None:
            return None
        return {
            "slug": slug,
            "type": page_type,
            "name": rule.get("name", slug),
            "tags": rule.get("topic_tags", []),
            "sources": rule.get("source_files", []),
            "body": _extract_body(text),
        }

    # Fallback: legacy glob. All rules treated as local (no universality).
    # Scan for the file whose derived slug (fm.slug/fm.name/stem) matches.
    filter_project = scope in ("project", "local-only") and project is not None
    for page_type in _RETRIEVAL_TYPES:
        type_dir = vault_root / "shared" / page_type
        if not type_dir.is_dir():
            continue
        for candidate in type_dir.glob("*.md"):
            try:
                text = candidate.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = parse_frontmatter(text)
            derived_slug = derive_rule_slug(fm, candidate.stem)
            if derived_slug != slug:
                continue
            if not is_consumer_visible(candidate, fm, vault_root):
                return None
            if filter_project and not _rule_belongs_to_project(fm, project):
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


def get_mnemo_topics(
    vault_root: Path,
    *,
    scope: str = "project",
    project: str | None = None,
) -> list[str]:
    """Return the sorted union of topic tags across rules visible at *scope*."""
    from mnemo.core import rule_activation

    idx = rule_activation.load_index(vault_root)
    if idx is not None and "rules" in idx:
        seen: set[str] = set()
        if scope == "vault":
            for rule in idx["rules"].values():
                seen.update(rule.get("topic_tags", []))
        elif scope == "local-only":
            if project is not None:
                seen.update(
                    idx.get("by_project", {}).get(project, {}).get("topics", [])
                )
        else:  # "project" default: local + universal
            if project is not None:
                seen.update(
                    idx.get("by_project", {}).get(project, {}).get("topics", [])
                )
            seen.update(idx.get("universal", {}).get("topics", []))
        return sorted(seen)

    # Fallback: legacy glob.
    filter_project = scope in ("project", "local-only") and project is not None
    seen = set()
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
            if filter_project and not _rule_belongs_to_project(fm, project):
                continue
            seen.update(topic_tags(fm))
    return sorted(seen)
