"""Regression: hooks must resolve project canonically so worktrees inherit the
main repo's reflex/enforce/enrich rules.

Pre-fix bug: ``user_prompt_submit`` and ``pre_tool_use`` used ``resolve_agent``,
which returns the worktree directory's basename (e.g. ``mnemo-recall-fix``).
The reflex / rule-activation indices key by canonical project name (``mnemo``),
so every worktree session logged ``index_missing`` (telemetry 2026-04-27 showed
105/858 reflex events silenced this way, 100% on every worktree project).

These tests build a real worktree on disk (``.git`` file with ``gitdir:``
pointer + ``commondir``) and assert that:

- UserPromptSubmit fires reflex on a project-local rule when invoked from the
  worktree.
- PreToolUse enforcement denies a Bash command when its enforce rule is local
  to the canonical project.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from mnemo.hooks import pre_tool_use
from mnemo.hooks import user_prompt_submit


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


def _seed_reflex_index_for_project(vault: Path, project: str) -> None:
    """Seed shared/feedback/ with a single high-signal rule local to *project*,
    plus noise rules so IDF stays meaningful, then build the index."""
    from mnemo.core.reflex.index import build_index, write_index
    feedback = vault / "shared" / "feedback"
    feedback.mkdir(parents=True, exist_ok=True)
    (feedback / "use-prisma-mock.md").write_text(
        "---\n"
        "name: use-prisma-mock\n"
        "description: Always use jest-mock-extended to mock Prisma in tests\n"
        "tags:\n  - prisma\n  - testing\n"
        f"sources:\n  - bots/{project}/memory/mock.md\n"
        "stability: stable\n"
        "---\n"
        "Mock the Prisma client in tests using jest-mock-extended.\n",
        encoding="utf-8",
    )
    for i, (slug, desc, tag) in enumerate([
        ("use-yarn", "Prefer yarn over npm for installs", "yarn"),
        ("commit-strategy", "Small atomic commits with clear messages", "git"),
        ("review-etiquette", "Be kind and specific in code reviews", "review"),
        ("python-style", "Follow PEP8 and black formatting", "python"),
        ("docs-style", "Write clear, concise documentation", "docs"),
    ]):
        (feedback / f"{slug}.md").write_text(
            f"---\nname: {slug}\ndescription: {desc}\n"
            f"tags:\n  - {tag}\n"
            f"sources:\n  - bots/noise{i}/memory/x.md\n"
            f"stability: stable\n---\nBody for {slug}.\n",
            encoding="utf-8",
        )
    write_index(vault, build_index(vault))


def _enable_reflex(vault: Path, monkeypatch) -> None:
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(vault / "mnemo.config.json"))
    (vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(vault),
        "reflex": {"enabled": True},
    }))


def _run_hook(stdin_payload: dict) -> tuple[int, str]:
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))), \
         patch("sys.stdout", out):
        rc = user_prompt_submit.main()
    return rc, out.getvalue()


def test_user_prompt_submit_fires_reflex_from_worktree(tmp_path, tmp_vault, monkeypatch):
    """Reflex must match a project-local rule even when cwd is a worktree."""
    worktree = _make_worktree(tmp_path, repo_name="myproject")
    _seed_reflex_index_for_project(tmp_vault, project="myproject")
    _enable_reflex(tmp_vault, monkeypatch)

    rc, stdout = _run_hook({
        "cwd": str(worktree),
        "session_id": "wt-sid",
        "prompt": "How do I mock prisma in a jest test with typescript",
    })
    assert rc == 0
    payload = json.loads(stdout)
    text = payload["hookSpecificOutput"]["additionalContext"]
    assert "[[use-prisma-mock]]" in text


def test_user_prompt_submit_pre_fix_would_silence_index_missing(tmp_path, tmp_vault, monkeypatch):
    """Sanity counter-test: the rule is NOT universal, so a wrong (non-canonical)
    project resolution must produce ``index_missing``. This guards the fix
    against silent regressions if someone reverts to ``resolve_agent``."""
    worktree = _make_worktree(tmp_path, repo_name="myproject")
    _seed_reflex_index_for_project(tmp_vault, project="myproject")
    _enable_reflex(tmp_vault, monkeypatch)

    # Force the worktree-basename resolution path (the pre-fix behaviour).
    # The hook does a local ``from mnemo.core.agent import ...`` so we patch
    # the attribute on the source module, which is what the import resolves.
    from mnemo.core import agent as agent_mod
    monkeypatch.setattr(
        agent_mod, "resolve_canonical_agent", agent_mod.resolve_agent
    )
    rc, stdout = _run_hook({
        "cwd": str(worktree),
        "session_id": "wt-sid-2",
        "prompt": "How do I mock prisma in a jest test with typescript",
    })
    assert rc == 0
    assert stdout == ""  # silenced — no reflex emit when project mismatches


def test_pre_tool_use_enforce_denies_from_worktree(tmp_path, tmp_vault, monkeypatch):
    """PreToolUse Bash enforcement on a project-local rule must fire on a worktree."""
    worktree = _make_worktree(tmp_path, repo_name="myproject")
    feedback = tmp_vault / "shared" / "feedback"
    feedback.mkdir(parents=True, exist_ok=True)
    (feedback / "no-rm-rf.md").write_text(
        "---\n"
        "name: never-rm-rf\n"
        "description: Never run destructive rm -rf in this repo\n"
        "type: feedback\n"
        "tags:\n  - safety\n"
        "sources:\n  - bots/myproject/memory/safety.md\n"
        "stability: stable\n"
        "enforce:\n"
        "  tool: Bash\n"
        "  deny_pattern: \"rm -rf\"\n"
        "  reason: \"blocked: rm -rf is denied for this repo\"\n"
        "---\nDo not rm -rf.\n",
        encoding="utf-8",
    )
    from mnemo.core import rule_activation
    rule_activation.write_index(tmp_vault, rule_activation.build_index(tmp_vault))

    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "enforcement": {"enabled": True},
    }))

    out = io.StringIO()
    payload = {
        "cwd": str(worktree),
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /tmp/foo"},
    }
    with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("sys.stdout", out):
        rc = pre_tool_use.main()
    # Hook returns deny exit code on enforce hit.
    assert rc != 0 or "blocked" in out.getvalue().lower()
