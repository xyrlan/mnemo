"""Tests for v0.7 scope semantics on MCP retrieval."""
from __future__ import annotations

import pytest

from mnemo.core.mcp.tools import _rule_in_scope


def _rule(projects, universal=False):
    return {"projects": list(projects), "universal": universal}


def test_scope_project_matches_local_rule():
    assert _rule_in_scope(_rule(["alpha"]), "alpha", "project") is True


def test_scope_project_matches_universal_rule_in_other_project():
    assert _rule_in_scope(_rule(["alpha"], universal=True), "beta", "project") is True


def test_scope_project_excludes_local_rule_of_other_project():
    assert _rule_in_scope(_rule(["alpha"]), "beta", "project") is False


def test_scope_local_only_excludes_universal():
    assert _rule_in_scope(_rule(["alpha"], universal=True), "beta", "local-only") is False


def test_scope_local_only_matches_local_rule():
    assert _rule_in_scope(_rule(["alpha"]), "alpha", "local-only") is True


def test_scope_vault_matches_everything():
    assert _rule_in_scope(_rule([]), None, "vault") is True
    assert _rule_in_scope(_rule(["alpha"]), "beta", "vault") is True
    assert _rule_in_scope(_rule(["alpha"], universal=True), None, "vault") is True


def test_scope_project_with_none_project_falls_through():
    # When project cannot be resolved, scope="project" returns universal rules only.
    assert _rule_in_scope(_rule([], universal=True), None, "project") is True
    assert _rule_in_scope(_rule(["alpha"]), None, "project") is False


from pathlib import Path
from mnemo.core.mcp.tools import list_rules_by_topic
from mnemo.core.rule_activation import build_index, write_index


def _write_feedback(vault: Path, stem: str, *, name: str, tags: list[str], sources: list[str]):
    fm_tags = "\n".join(f"  - {t}" for t in tags)
    fm_sources = "\n".join(f"  - {s}" for s in sources)
    content = (
        "---\n"
        f"name: {name}\n"
        "stability: stable\n"
        f"tags:\n{fm_tags}\n"
        f"sources:\n{fm_sources}\n"
        "---\n\n"
        f"Body of {name}.\n"
    )
    target = vault / "shared" / "feedback" / f"{stem}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def test_list_rules_by_topic_returns_local_and_universal(tmp_vault):
    _write_feedback(tmp_vault, "local", name="local-rule",
                    tags=["git", "auto-promoted"],
                    sources=["bots/alpha/memory/l.md"])
    _write_feedback(tmp_vault, "uni", name="universal-rule",
                    tags=["git", "auto-promoted"],
                    sources=["bots/alpha/memory/u.md", "bots/beta/memory/u.md"])
    write_index(tmp_vault, build_index(tmp_vault))

    # From project beta, "local-rule" is alpha-only, "universal-rule" is cross-project
    results = list_rules_by_topic(tmp_vault, "git", scope="project", project="beta")
    slugs = {r["slug"] for r in results}
    assert "universal-rule" in slugs
    assert "local-rule" not in slugs


def test_list_rules_by_topic_local_only_excludes_universal(tmp_vault):
    _write_feedback(tmp_vault, "uni", name="universal-rule",
                    tags=["git", "auto-promoted"],
                    sources=["bots/alpha/memory/u.md", "bots/beta/memory/u.md"])
    write_index(tmp_vault, build_index(tmp_vault))

    results = list_rules_by_topic(tmp_vault, "git", scope="local-only", project="gamma")
    assert results == []


def test_list_rules_by_topic_falls_back_when_index_missing(tmp_vault):
    """No .mnemo/rule-activation-index.json present — function still returns from glob+parse."""
    _write_feedback(tmp_vault, "local", name="local-rule",
                    tags=["git", "auto-promoted"],
                    sources=["bots/alpha/memory/l.md"])
    # No write_index call — fallback path must work; slug derived from fm.name
    results = list_rules_by_topic(tmp_vault, "git", scope="project", project="alpha")
    assert any(r["slug"] == "local-rule" for r in results)


from mnemo.core.mcp.tools import read_mnemo_rule


def test_read_mnemo_rule_returns_universal_from_foreign_project(tmp_vault):
    _write_feedback(tmp_vault, "universal-rule", name="universal-rule",
                    tags=["git", "auto-promoted"],
                    sources=["bots/alpha/memory/u.md", "bots/beta/memory/u.md"])
    write_index(tmp_vault, build_index(tmp_vault))
    result = read_mnemo_rule(tmp_vault, "universal-rule", scope="project", project="gamma")
    assert result is not None
    assert result["slug"] == "universal-rule"


def test_read_mnemo_rule_local_only_rejects_universal_from_foreign_project(tmp_vault):
    _write_feedback(tmp_vault, "universal-rule", name="universal-rule",
                    tags=["git", "auto-promoted"],
                    sources=["bots/alpha/memory/u.md", "bots/beta/memory/u.md"])
    write_index(tmp_vault, build_index(tmp_vault))
    assert read_mnemo_rule(tmp_vault, "universal-rule",
                           scope="local-only", project="gamma") is None


def test_read_mnemo_rule_returns_none_for_unknown_slug(tmp_vault):
    write_index(tmp_vault, build_index(tmp_vault))
    assert read_mnemo_rule(tmp_vault, "ghost", scope="vault") is None


from mnemo.core.mcp.tools import get_mnemo_topics


def test_get_mnemo_topics_project_includes_universal(tmp_vault):
    _write_feedback(tmp_vault, "uni", name="universal-rule",
                    tags=["git", "auto-promoted"],
                    sources=["bots/alpha/memory/u.md", "bots/beta/memory/u.md"])
    _write_feedback(tmp_vault, "local", name="local-rule",
                    tags=["code-style", "auto-promoted"],
                    sources=["bots/alpha/memory/l.md"])
    write_index(tmp_vault, build_index(tmp_vault))
    topics = get_mnemo_topics(tmp_vault, scope="project", project="gamma")
    # gamma has no local rules but inherits universal
    assert "git" in topics
    assert "code-style" not in topics


def test_get_mnemo_topics_vault_union(tmp_vault):
    _write_feedback(tmp_vault, "a", name="a-rule",
                    tags=["x", "auto-promoted"],
                    sources=["bots/alpha/memory/a.md"])
    _write_feedback(tmp_vault, "b", name="b-rule",
                    tags=["y", "auto-promoted"],
                    sources=["bots/beta/memory/b.md"])
    write_index(tmp_vault, build_index(tmp_vault))
    topics = get_mnemo_topics(tmp_vault, scope="vault")
    assert "x" in topics and "y" in topics


def test_fallback_slug_matches_fast_path_when_name_differs_from_stem(tmp_vault):
    """Slug derivation must be consistent between fast-path (index) and fallback.

    Regression: when fm.name differs from file stem, the fast path returned the
    name-derived slug, but the fallback returned md.stem. Callers couldn't chain
    list_rules_by_topic -> read_mnemo_rule when the index was absent.
    """
    # Write a feedback file where stem != name
    (tmp_vault / "shared" / "feedback").mkdir(parents=True, exist_ok=True)
    (tmp_vault / "shared" / "feedback" / "feedback_foo.md").write_text(
        "---\n"
        "name: use-tabs\n"
        "stability: stable\n"
        "tags:\n  - code-style\n"
        "sources:\n  - bots/alpha/memory/f.md\n"
        "---\n\n"
        "Body.\n"
    )

    # Fast path: write index, query should return slug "use-tabs"
    write_index(tmp_vault, build_index(tmp_vault))
    fast = {r["slug"] for r in list_rules_by_topic(
        tmp_vault, "code-style", scope="local-only", project="alpha"
    )}
    assert fast == {"use-tabs"}

    # Fallback path: delete index, query should return the SAME slug
    (tmp_vault / ".mnemo" / "rule-activation-index.json").unlink()
    fallback = {r["slug"] for r in list_rules_by_topic(
        tmp_vault, "code-style", scope="local-only", project="alpha"
    )}
    assert fallback == fast, f"fallback returned different slug: {fallback} vs {fast}"

    # And read_mnemo_rule must find the rule body via the slug from either path
    body = read_mnemo_rule(tmp_vault, "use-tabs", scope="local-only", project="alpha")
    assert body is not None
    assert body["slug"] == "use-tabs"
