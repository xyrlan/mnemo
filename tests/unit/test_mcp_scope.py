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
    # No write_index call — fallback path must work
    results = list_rules_by_topic(tmp_vault, "git", scope="project", project="alpha")
    assert any(r["slug"] == "local" for r in results)
