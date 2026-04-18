"""End-to-end scope-model regression: local-only must equal v0.6.2 project filter.

v0.7 unifies slug derivation across both the index and fallback paths. We compare
the set of rules returned (using stem as the common identifier) between the v0.7
scope="local-only" path and a faithful replica of the v0.6.2 logic. The data is
set up with stem == name so the two slug derivations converge trivially — the
test then focuses purely on the filter-decision contract.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.mcp.tools import list_rules_by_topic, _rule_belongs_to_project
from mnemo.core.filters import parse_frontmatter, is_consumer_visible, topic_tags
from mnemo.core.rule_activation import build_index, write_index


def _write_feedback(vault: Path, stem: str, *, name: str, tags: list[str], sources: list[str]):
    fm_tags = "\n".join(f"  - {t}" for t in tags)
    fm_sources = "\n".join(f"  - {s}" for s in sources)
    (vault / "shared" / "feedback").mkdir(parents=True, exist_ok=True)
    (vault / "shared" / "feedback" / f"{stem}.md").write_text(
        "---\n"
        f"name: {name}\n"
        "stability: stable\n"
        f"tags:\n{fm_tags}\n"
        f"sources:\n{fm_sources}\n"
        "---\n\n"
        f"Body of {name}.\n"
    )


def _legacy_list(vault: Path, topic: str, project: str) -> set[str]:
    """Replica of the v0.6.2 list_rules_by_topic project-filter logic."""
    out: set[str] = set()
    for md in (vault / "shared" / "feedback").glob("*.md"):
        fm = parse_frontmatter(md.read_text())
        if not is_consumer_visible(md, fm, vault):
            continue
        if topic not in topic_tags(fm):
            continue
        if not _rule_belongs_to_project(fm, project):
            continue
        out.add(md.stem)
    return out


def test_local_only_matches_legacy_v062_behavior(tmp_vault):
    # stem == name — so both slug-derivation paths produce the same identifier
    _write_feedback(tmp_vault, "rule-one", name="rule-one",
                    tags=["git", "auto-promoted"],
                    sources=["bots/alpha/memory/a.md"])
    _write_feedback(tmp_vault, "rule-two", name="rule-two",
                    tags=["git", "auto-promoted"],
                    sources=["bots/beta/memory/b.md"])
    _write_feedback(tmp_vault, "rule-three", name="rule-three",
                    tags=["git", "auto-promoted"],
                    sources=["bots/alpha/memory/c.md", "bots/beta/memory/c.md"])
    write_index(tmp_vault, build_index(tmp_vault))

    for project in ("alpha", "beta", "gamma"):
        legacy = _legacy_list(tmp_vault, "git", project)
        new = {r["slug"] for r in list_rules_by_topic(
            tmp_vault, "git", scope="local-only", project=project
        )}
        assert new == legacy, f"mismatch for project {project}: {new} vs {legacy}"
