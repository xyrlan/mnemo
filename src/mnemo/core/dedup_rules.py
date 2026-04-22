"""Name-keyed dedup for already-promoted shared/<type>/*.md rule files.

LLM-generated slugs drift across extraction runs for the same logical rule,
so files with identical ``name:`` accumulate in ``shared/<type>/``. This
module plans a merge: pick canonical by ``max(len(sources[]))`` (ties → newer
``extracted_at``), union ``sources[]`` + frontmatter project attribution,
recompute ``projects[]``, delete the rest. Only the ``sources:`` and
``projects:`` frontmatter blocks are rewritten — every other key and the
body are preserved byte-for-byte (W2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mnemo.core.filters import parse_frontmatter as _parse_rule_fm
from mnemo.core.rule_activation.index import projects_for_rule


def normalize_name(raw: str) -> str:
    """Case + whitespace normalization used by plan_dedup and the doctor check."""
    return " ".join(raw.strip().lower().split())


_FM_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


def _parse_fm(text: str) -> tuple[dict, str]:
    """Parse mnemo-shape frontmatter + return raw body.

    Delegates parsing to :func:`mnemo.core.filters.parse_frontmatter` —
    the canonical parser used by rule-activation. We only add body
    extraction, which filters.parse_frontmatter doesn't return.
    """
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    return _parse_rule_fm(text), m.group(2)


@dataclass
class DedupGroup:
    canonical: Path
    duplicates: list[Path]
    merged_sources: list[str] = field(default_factory=list)
    merged_projects: list[str] = field(default_factory=list)
    merged_fm_projects: list[str] = field(default_factory=list)  # W4: unioned fm project(s)


@dataclass
class DedupPlan:
    vault_root: Path
    groups: list[DedupGroup]

    def apply(self) -> None:
        for g in self.groups:
            _merge_group_inplace(g)


def _fm_projects(fm: dict) -> list[str]:
    raw = fm.get("projects")
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, str) and p]
    single = fm.get("project")
    if isinstance(single, str) and single:
        return [single]
    return []


def plan_dedup(vault_root: Path) -> DedupPlan:
    shared = vault_root / "shared"
    if not shared.is_dir():
        return DedupPlan(vault_root=vault_root, groups=[])

    buckets: dict[tuple[str, str], list[tuple[Path, dict]]] = {}
    for type_dir in sorted(p for p in shared.iterdir() if p.is_dir()):
        for md in sorted(type_dir.glob("*.md")):
            try:
                fm, _ = _parse_fm(md.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            name = fm.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            key = (type_dir.name, normalize_name(name))
            buckets.setdefault(key, []).append((md, fm))

    groups: list[DedupGroup] = []
    for entries in buckets.values():
        if len(entries) < 2:
            continue
        # W3: canonical = most sources; tie-break = newer extracted_at.
        entries.sort(
            key=lambda e: (
                len(e[1].get("sources") or []),
                str(e[1].get("extracted_at") or ""),
            ),
            reverse=True,
        )
        canonical_path, _ = entries[0]
        duplicate_paths = [p for p, _ in entries[1:]]

        merged_sources: list[str] = []
        merged_fm_projects: list[str] = []
        for _p, fm in entries:
            for s in (fm.get("sources") or []):
                if isinstance(s, str) and s not in merged_sources:
                    merged_sources.append(s)
            for proj in _fm_projects(fm):
                if proj not in merged_fm_projects:
                    merged_fm_projects.append(proj)

        merged_projects = projects_for_rule(
            merged_sources,
            frontmatter={"projects": merged_fm_projects} if merged_fm_projects else None,
        )
        groups.append(DedupGroup(
            canonical=canonical_path,
            duplicates=duplicate_paths,
            merged_sources=merged_sources,
            merged_projects=merged_projects,
            merged_fm_projects=merged_fm_projects,
        ))
    return DedupPlan(vault_root=vault_root, groups=groups)


# ---------------------------------------------------------------------------
# W2: surgical frontmatter rewrite — replace only sources:/projects: blocks,
# leave every other line untouched so diffs stay quiet and quoting survives.
# ---------------------------------------------------------------------------


def _rewrite_block(fm_text: str, key: str, lines: list[str]) -> str:
    """Replace an existing `key:` block (`key: [...]` or `key:\n  - a\n  - b`)
    with the given rendered lines. If the key is absent, append at the end
    of the frontmatter. The new block is always emitted in list form.

    Assumes mnemo-writer frontmatter shape: block lists contain only ``  - item``
    lines (no comments, no blank lines interleaved). Hand-edited files with such
    interior lines will not be rewritten cleanly — the orphan content stays past
    the replaced block. Fine for the mnemo pipeline (it owns the writer); flagged
    here so future callers know the constraint.
    """
    block_re = re.compile(
        rf"(?m)^{re.escape(key)}:[ \t]*(?:\[\])?[ \t]*\n(?:[ \t]+-[^\n]*\n?)*",
    )
    new_block = f"{key}: []" if not lines else f"{key}:\n" + "\n".join(f"  - {v}" for v in lines)
    if block_re.search(fm_text):
        return block_re.sub(lambda _m: new_block + "\n", fm_text, count=1).rstrip() + "\n"
    # append — ensure single trailing newline
    return fm_text.rstrip() + "\n" + new_block + "\n"


def _merge_group_inplace(g: DedupGroup) -> None:
    text = g.canonical.read_text(encoding="utf-8")
    m = _FM_RE.match(text)
    if not m:
        return  # canonical has no frontmatter; skip rather than corrupt
    fm_text, rest = m.group(1), m.group(2)

    fm_text = _rewrite_block(fm_text, "sources", g.merged_sources)
    if g.merged_fm_projects:
        fm_text = _rewrite_block(fm_text, "projects", g.merged_fm_projects)

    new_text = "---\n" + fm_text.rstrip() + "\n---\n" + rest
    g.canonical.write_text(new_text, encoding="utf-8")
    for d in g.duplicates:
        d.unlink()
