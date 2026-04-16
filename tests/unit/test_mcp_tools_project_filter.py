"""Tests for MCP project-filter behavior (v0.5.2)."""
from __future__ import annotations

from mnemo.core.mcp.tools import _rule_belongs_to_project


def test_rule_belongs_single_project_match():
    fm = {"sources": ["bots/mnemo/memory/m.md"]}
    assert _rule_belongs_to_project(fm, "mnemo") is True


def test_rule_belongs_single_project_no_match():
    fm = {"sources": ["bots/other-proj/memory/m.md"]}
    assert _rule_belongs_to_project(fm, "mnemo") is False


def test_rule_belongs_multi_project_sources():
    fm = {"sources": ["bots/alpha/memory/a.md", "bots/beta/memory/b.md"]}
    assert _rule_belongs_to_project(fm, "alpha") is True
    assert _rule_belongs_to_project(fm, "beta") is True
    assert _rule_belongs_to_project(fm, "gamma") is False


def test_rule_belongs_no_sources_returns_false():
    assert _rule_belongs_to_project({}, "mnemo") is False
    assert _rule_belongs_to_project({"sources": []}, "mnemo") is False
    assert _rule_belongs_to_project({"sources": None}, "mnemo") is False


def test_rule_belongs_partial_prefix_no_false_positive():
    """'bots/mn/' must NOT match project 'mnemo'."""
    fm = {"sources": ["bots/mn/memory/m.md"]}
    assert _rule_belongs_to_project(fm, "mnemo") is False


from unittest.mock import patch
from pathlib import Path

from mnemo.core.mcp.tools import _resolve_current_project


def test_resolve_current_project_returns_project_name(tmp_vault):
    """When cwd is inside a git repo, return the sanitized repo name."""
    git_dir = tmp_vault / ".git"
    git_dir.mkdir()
    with patch("mnemo.core.mcp.tools.Path") as MockPath:
        MockPath.cwd.return_value = tmp_vault
        result = _resolve_current_project(tmp_vault)
    assert result == "vault"  # tmp_vault dir name is "vault" per conftest


def test_resolve_current_project_returns_none_on_failure(tmp_vault):
    with patch("mnemo.core.mcp.tools.resolve_agent", side_effect=RuntimeError("boom")):
        result = _resolve_current_project(tmp_vault)
    assert result is None


from mnemo.core.mcp.tools import list_rules_by_topic


def _write_page(
    vault: Path,
    page_type: str,
    slug: str,
    *,
    tags: list[str],
    sources: list[str],
    stability: str = "stable",
    body: str = "the rule body\n",
) -> Path:
    target_dir = vault / "shared" / page_type
    target_dir.mkdir(parents=True, exist_ok=True)
    sources_yaml = "\n".join(f"  - {s}" for s in sources)
    tags_yaml = "\n".join(f"  - {t}" for t in tags)
    text = (
        "---\n"
        f"name: {slug.replace('-', ' ').title()}\n"
        f"description: a description for {slug}\n"
        f"type: {page_type}\n"
        f"stability: {stability}\n"
        "sources:\n"
        f"{sources_yaml}\n"
        "tags:\n"
        f"{tags_yaml}\n"
        "---\n\n"
        f"{body}"
    )
    target = target_dir / f"{slug}.md"
    target.write_text(text)
    return target


# --- list_rules_by_topic project filter ---


def test_list_rules_by_topic_default_scope_is_project(tmp_vault):
    """NON-NEGOTIABLE: default scope='project' filters to current project."""
    _write_page(
        tmp_vault, "feedback", "my-rule",
        tags=["auto-promoted", "git"],
        sources=["bots/alpha/memory/m.md"],
    )
    _write_page(
        tmp_vault, "feedback", "other-rule",
        tags=["auto-promoted", "git"],
        sources=["bots/beta/memory/m.md"],
    )
    result = list_rules_by_topic(tmp_vault, "git", project="alpha")
    slugs = [r["slug"] for r in result]
    assert "my-rule" in slugs
    assert "other-rule" not in slugs


def test_list_rules_by_topic_vault_scope_returns_all(tmp_vault):
    _write_page(
        tmp_vault, "feedback", "alpha-rule",
        tags=["auto-promoted", "git"],
        sources=["bots/alpha/memory/m.md"],
    )
    _write_page(
        tmp_vault, "feedback", "beta-rule",
        tags=["auto-promoted", "git"],
        sources=["bots/beta/memory/m.md"],
    )
    result = list_rules_by_topic(tmp_vault, "git", scope="vault", project="alpha")
    slugs = [r["slug"] for r in result]
    assert "alpha-rule" in slugs
    assert "beta-rule" in slugs


def test_list_rules_by_topic_project_none_falls_back_to_vault(tmp_vault):
    """When project resolution fails (None), scope='project' silently becomes vault."""
    _write_page(
        tmp_vault, "feedback", "any-rule",
        tags=["auto-promoted", "git"],
        sources=["bots/alpha/memory/m.md"],
    )
    result = list_rules_by_topic(tmp_vault, "git", scope="project", project=None)
    assert len(result) == 1
    assert result[0]["slug"] == "any-rule"


def test_list_rules_by_topic_multi_source_rule_matches_any_project(tmp_vault):
    """Rule with sources from alpha AND beta appears for both projects."""
    _write_page(
        tmp_vault, "feedback", "shared-rule",
        tags=["auto-promoted", "git"],
        sources=["bots/alpha/memory/a.md", "bots/beta/memory/b.md"],
    )
    result_alpha = list_rules_by_topic(tmp_vault, "git", project="alpha")
    result_beta = list_rules_by_topic(tmp_vault, "git", project="beta")
    assert len(result_alpha) == 1
    assert len(result_beta) == 1
