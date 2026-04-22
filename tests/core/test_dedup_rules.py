"""Name-keyed rule dedup: plan a merge for shared/*.md files sharing a `name:`."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core.dedup_rules import DedupPlan, plan_dedup


def _write(
    p: Path,
    name: str,
    *,
    sources: list[str],
    extracted_at: str,
    body: str = "body",
    frontmatter_project: str | None = None,
) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name!r}", "description: 'd'", "type: feedback",
             f"extracted_at: {extracted_at}", "stability: stable"]
    if frontmatter_project:
        lines.append(f"project: {frontmatter_project}")
    if sources:
        lines.append("sources:")
        lines.extend(f"  - {s}" for s in sources)
    else:
        lines.append("sources: []")
    lines.extend(["tags:", "  - git", "---", body, ""])
    p.write_text("\n".join(lines), encoding="utf-8")


def test_no_duplicates_is_empty_plan(tmp_path):
    shared = tmp_path / "shared" / "feedback"
    _write(shared / "a.md", "Rule A", sources=["bots/x/a.md"], extracted_at="2026-04-19T10:00:00")
    _write(shared / "b.md", "Rule B", sources=["bots/y/b.md"], extracted_at="2026-04-19T11:00:00")
    plan = plan_dedup(tmp_path)
    assert plan.groups == []


def test_canonical_is_most_sources_then_recency(tmp_path):
    shared = tmp_path / "shared" / "feedback"
    # b.md has the most sources (2) — must win even though c.md is more recent with 1 source.
    _write(shared / "a.md", "Dup", sources=["bots/x/a.md"], extracted_at="2026-04-19T10:00:00")
    _write(shared / "b.md", "Dup", sources=["bots/x/b.md", "bots/x/b2.md"], extracted_at="2026-04-20T10:00:00")
    _write(shared / "c.md", "Dup", sources=["bots/y/c.md"], extracted_at="2026-04-21T10:00:00")
    plan = plan_dedup(tmp_path)
    assert len(plan.groups) == 1
    g = plan.groups[0]
    assert g.canonical.name == "b.md"
    assert sorted(p.name for p in g.duplicates) == ["a.md", "c.md"]
    assert sorted(g.merged_sources) == ["bots/x/a.md", "bots/x/b.md", "bots/x/b2.md", "bots/y/c.md"]
    assert sorted(g.merged_projects) == ["x", "y"]


def test_canonical_tiebreak_on_recency(tmp_path):
    shared = tmp_path / "shared" / "feedback"
    # Equal source counts → newer extracted_at wins.
    _write(shared / "a.md", "Dup", sources=["bots/x/a.md"], extracted_at="2026-04-19T10:00:00")
    _write(shared / "b.md", "Dup", sources=["bots/y/b.md"], extracted_at="2026-04-20T10:00:00")
    plan = plan_dedup(tmp_path)
    assert plan.groups[0].canonical.name == "b.md"


def test_name_match_is_case_and_whitespace_insensitive(tmp_path):
    shared = tmp_path / "shared" / "feedback"
    _write(shared / "a.md", "  Stacked PRs  ", sources=["bots/x/a.md"], extracted_at="2026-04-19T10:00:00")
    _write(shared / "b.md", "stacked prs",     sources=["bots/y/b.md"], extracted_at="2026-04-19T11:00:00")
    plan = plan_dedup(tmp_path)
    assert len(plan.groups) == 1


def test_types_do_not_cross(tmp_path):
    (tmp_path / "shared" / "feedback").mkdir(parents=True)
    (tmp_path / "shared" / "project").mkdir(parents=True)
    _write(tmp_path / "shared" / "feedback" / "a.md", "X", sources=["bots/x/a.md"], extracted_at="2026-04-19T10:00:00")
    (tmp_path / "shared" / "project" / "a.md").write_text(
        "---\nname: 'X'\ndescription: 'd'\ntype: project\nextracted_at: 2026-04-19T10:00:00\n"
        "stability: stable\nsources:\n  - bots/y/p.md\ntags: []\n---\nbody\n",
        encoding="utf-8",
    )
    plan = plan_dedup(tmp_path)
    assert plan.groups == []


def test_apply_preserves_frontmatter_byte_identity_for_unchanged_keys(tmp_path):
    """W2: only sources:/projects: blocks may change; name, description,
    extracted_at and body must survive merge byte-for-byte."""
    shared = tmp_path / "shared" / "feedback"
    _write(shared / "a.md", "Dup", sources=["bots/x/a.md"], extracted_at="2026-04-19T10:00:00", body="old body")
    _write(shared / "b.md", "Dup", sources=["bots/y/b1.md", "bots/y/b2.md"], extracted_at="2026-04-20T10:00:00", body="newer body")

    plan_dedup(tmp_path).apply()
    canonical_after = (shared / "b.md").read_text(encoding="utf-8")

    assert "name: 'Dup'" in canonical_after                          # quoting preserved
    assert "description: 'd'" in canonical_after
    assert "extracted_at: 2026-04-20T10:00:00" in canonical_after
    assert "newer body" in canonical_after                           # body preserved
    assert "bots/x/a.md" in canonical_after                          # merged in
    assert "bots/y/b1.md" in canonical_after and "bots/y/b2.md" in canonical_after
    assert not (shared / "a.md").exists()


def test_apply_unions_frontmatter_project_across_group(tmp_path):
    """W4: if only a duplicate carries frontmatter project:, the merged
    canonical must inherit it (else Task 1's benefit is lost on merge)."""
    shared = tmp_path / "shared" / "feedback"
    _write(shared / "a.md", "Dup", sources=[], extracted_at="2026-04-19T10:00:00",
           frontmatter_project="mnemo")
    _write(shared / "b.md", "Dup", sources=[], extracted_at="2026-04-20T10:00:00")  # canonical (newer, equal sources)

    plan_dedup(tmp_path).apply()
    canonical = (shared / "b.md").read_text(encoding="utf-8")
    assert "project: mnemo" in canonical or "projects:" in canonical
