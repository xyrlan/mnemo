from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit._export_fixtures import write_rule


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


def test_no_manifest_prints_nothing(tmp_vault: Path, repo: Path, capsys):
    from mnemo.cli.commands.status import _print_export_status

    _print_export_status(tmp_vault)
    assert capsys.readouterr().out == ""


def test_up_to_date_and_stale(tmp_vault: Path, repo: Path, capsys, monkeypatch):
    from mnemo.core import export as export_mod
    from mnemo.cli.commands.status import _print_export_status

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    write_rule(tmp_vault, slug="a")
    export_mod.run_export(tmp_vault, project="app", repo_root=repo)

    _print_export_status(tmp_vault)
    assert capsys.readouterr().out == "\nExport: 1 rule → .claude/rules/mnemo.md (up to date)\n"

    write_rule(tmp_vault, slug="a", body="changed\n")
    write_rule(tmp_vault, slug="b")
    _print_export_status(tmp_vault)
    assert capsys.readouterr().out == (
        "\nExport: 1 rule → .claude/rules/mnemo.md (2 differ from the vault now, run mnemo export)\n"
    )


def test_status_command_includes_export_line(tmp_vault: Path, repo: Path, capsys, monkeypatch):
    from mnemo import cli
    from mnemo.core import export as export_mod

    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    write_rule(tmp_vault, slug="a")
    export_mod.run_export(tmp_vault, project="app", repo_root=repo)
    assert cli.main(["status"]) == 0
    assert "Export: 1 rule → .claude/rules/mnemo.md (up to date)" in capsys.readouterr().out


def test_export_status_resolves_canonical_project_from_worktree(
    tmp_path: Path, tmp_vault: Path, capsys, monkeypatch
):
    """Standing in a git worktree, status must find the manifest `mnemo export`
    wrote under the *main repo's* name — both resolve the project the same
    way (`resolve_canonical_agent`), following the worktree's `.git` file
    back to the main repo rather than using the worktree's own directory name.
    """
    from mnemo.core import export as export_mod
    from mnemo.cli.commands.status import _print_export_status

    main_git = tmp_path / "main" / ".git"
    worktree_gitdir = main_git / "worktrees" / "feat"
    worktree_gitdir.mkdir(parents=True)
    (worktree_gitdir / "commondir").write_text("../..\n", encoding="utf-8")

    feat_dir = tmp_path / "feat"
    feat_dir.mkdir(parents=True)
    (feat_dir / ".git").write_text(f"gitdir: {worktree_gitdir}\n", encoding="utf-8")

    monkeypatch.chdir(feat_dir)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})

    write_rule(tmp_vault, slug="a", projects=("main",))
    export_mod.run_export(tmp_vault, project="main", repo_root=feat_dir)

    _print_export_status(tmp_vault)
    assert capsys.readouterr().out == "\nExport: 1 rule → .claude/rules/mnemo.md (up to date)\n"
