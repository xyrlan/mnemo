"""Regression (#225): the MCP server must resolve the project canonically.

Pre-fix bug: ``_resolve_current_project`` used ``resolve_agent``, which returns
the worktree directory's basename (e.g. ``mnemo-wt-225``). It backs
``list_rules_by_topic`` and ``read_mnemo_rule`` — the two tools the injected
prompt tells every session to call *before writing code* — so a dispatched
child scoped its rule lookup to a namespace that has never had a rule written
to it. Measured on the real vault from ``mnemo-wt-225``: 21 topics visible
instead of 77.

The hooks were already canonicalised (see
``test_hooks_worktree_canonical.py``); the MCP read path was missed by that
sweep. These tests build a real worktree on disk (``.git`` file with a
``gitdir:`` pointer + ``commondir``) and assert the project resolves to the
main repo, that project-local rules are actually reachable through the public
tool, and — as a counter-test — that reverting to ``resolve_agent`` loses them.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mnemo.core.mcp.tools import _resolve_current_project, list_rules_by_topic


def _make_worktree(tmp_path: Path, *, repo_name: str = "myproject") -> Path:
    """Build a main repo + one worktree that resolves canonically to *repo_name*."""
    main_repo = tmp_path / repo_name
    main_repo.mkdir()
    git_dir = main_repo / ".git"
    git_dir.mkdir()
    wt_dir = git_dir / "worktrees" / "feature-x"
    wt_dir.mkdir(parents=True)
    (wt_dir / "commondir").write_text("../..\n")
    worktree = tmp_path / f"{repo_name}-feature-x"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_dir}\n")
    return worktree


def _seed_local_rule(vault: Path, project: str, *, slug: str = "use-prisma-mock") -> None:
    """Write one rule local to *project* (not universal) and index it."""
    feedback = vault / "shared" / "feedback"
    feedback.mkdir(parents=True, exist_ok=True)
    (feedback / f"{slug}.md").write_text(
        "---\n"
        f"name: {slug}\n"
        "description: Always use jest-mock-extended to mock Prisma in tests\n"
        "type: feedback\n"
        "tags:\n  - testing\n"
        f"sources:\n  - bots/{project}/memory/mock.md\n"
        "stability: stable\n"
        "---\n"
        "Mock the Prisma client in tests using jest-mock-extended.\n",
        encoding="utf-8",
    )


def test_resolve_current_project_is_canonical_from_a_worktree(tmp_path, tmp_vault):
    """cwd inside a worktree must resolve to the MAIN repo name, not the tree's."""
    worktree = _make_worktree(tmp_path, repo_name="myproject")
    with patch("mnemo.core.mcp.tools.Path") as MockPath:
        MockPath.cwd.return_value = worktree
        assert _resolve_current_project(tmp_vault) == "myproject"


def test_worktree_lookup_reaches_project_local_rules(tmp_path, tmp_vault):
    """The public tool must return a rule local to the canonical project."""
    worktree = _make_worktree(tmp_path, repo_name="myproject")
    _seed_local_rule(tmp_vault, "myproject")

    with patch("mnemo.core.mcp.tools.Path") as MockPath:
        MockPath.cwd.return_value = worktree
        project = _resolve_current_project(tmp_vault)

    rules = list_rules_by_topic(
        tmp_vault, "testing", scope="local-only", project=project
    )
    assert [r["slug"] for r in rules] == ["use-prisma-mock"]


def test_pre_fix_naive_resolution_loses_the_rule(tmp_path, tmp_vault):
    """Counter-test: the rule is NOT universal, so the worktree basename must
    find nothing. Guards the fix against a silent revert to ``resolve_agent``."""
    worktree = _make_worktree(tmp_path, repo_name="myproject")
    _seed_local_rule(tmp_vault, "myproject")

    from mnemo.core import agent as agent_mod

    with patch("mnemo.core.mcp.tools.Path") as MockPath, patch.object(
        agent_mod, "resolve_canonical_agent", agent_mod.resolve_agent
    ):
        MockPath.cwd.return_value = worktree
        project = _resolve_current_project(tmp_vault)

    assert project == "myproject-feature-x"  # the pre-fix answer
    rules = list_rules_by_topic(
        tmp_vault, "testing", scope="local-only", project=project
    )
    assert rules == []
