"""The "Existing rules" hint shown in the consolidation user message.

Each extraction used to mint a fresh slug for a rule the vault already held,
so ``source_count`` never accrued and near-duplicate families grew. Listing the
live and staged slugs for the chunk's projects lets the model reinforce an
existing rule instead; ``inbox/dedup._detect_similar_existing`` (Task 7) is
the mechanical backstop when it does not.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.filters import parse_frontmatter
from mnemo.core.rule_activation import is_universal, projects_for_rule

MAX_ENTRIES = 80
_UNIVERSAL_THRESHOLD = 2


def _slug_for(frontmatter: dict, stem: str) -> str:
    """The identifier the model must echo to reinforce this page.

    Deliberately NOT ``filters.derive_rule_slug``: that helper falls back to
    the human-readable ``name`` before the stem, which is right for matching a
    legacy page but wrong here — we are handing the model a slug to emit, and a
    rule page is always written to disk under its slug. A display name like
    "Use yarn" would be echoed back verbatim and mint a new page.
    """
    slug = frontmatter.get("slug")
    if isinstance(slug, str) and slug.strip():
        return slug.strip()
    return stem


def _collect(vault_root: Path, kind: str) -> list[tuple[str, str, list[str], int]]:
    out = []
    for d in (vault_root / "shared" / kind, vault_root / "shared" / "_inbox" / kind):
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            if md.name.endswith(".proposed.md") or md.name.endswith(".update-proposed.md"):
                continue
            try:
                fm = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            sources = fm.get("sources") or []
            if isinstance(sources, str):
                sources = [sources]
            sources = [s for s in sources if isinstance(s, str)]
            out.append((_slug_for(fm, md.stem), str(fm.get("name") or md.stem),
                        projects_for_rule(sources), len(sources)))
    return out


def existing_rules_fragment(vault_root: Path | None, kind: str, *, agents: set[str]) -> str:
    """Render the slug list for *kind*, scoped to *agents* (plus universal rules)."""
    if vault_root is None:
        return ""
    rows = []
    for slug, name, projects, count in _collect(vault_root, kind):
        if agents and projects and not (set(projects) & agents) \
                and not is_universal(projects, _UNIVERSAL_THRESHOLD):
            continue
        rows.append((count, slug, name))
    if not rows:
        return ""
    rows.sort(key=lambda r: (-r[0], r[1]))
    lines = [f"- {slug} — {name}" for _, slug, name in rows[:MAX_ENTRIES]]
    return (
        f"Existing rules for {kind} (REUSE the slug when your page states the same "
        f"rule — only mint a new slug for a genuinely new rule):\n"
        + "\n".join(lines)
        + "\n\n"
    )
