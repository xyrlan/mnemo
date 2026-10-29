"""mnemo migrate-worktree-briefings moves orphan briefings to canonical agent dir."""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.commands import migrate_worktree_briefings as cmd_mod


def _make_repo_with_worktree_briefings(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Returns (vault, main_repo, worktree)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    main = tmp_path / "myproj"
    main.mkdir()
    (main / ".git").mkdir()
    wt_gitdir = main / ".git" / "worktrees" / "feature-x"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "myproj-wt-feature-x"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")

    # Orphan briefing under the worktree's old agent dir.
    orphan_dir = vault / "bots" / "myproj-wt-feature-x" / "briefings" / "sessions"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "session-1.md").write_text("orphan briefing", encoding="utf-8")

    return vault, main, worktree


def test_migrate_dry_run_lists_moves_only(tmp_path: Path, capsys, monkeypatch) -> None:
    vault, main, _wt = _make_repo_with_worktree_briefings(tmp_path)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=True, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "would move" in out
    assert "session-1.md" in out
    # No move actually happened.
    assert (vault / "bots" / "myproj-wt-feature-x" / "briefings" / "sessions" / "session-1.md").exists()
    assert not (vault / "bots" / "myproj" / "briefings" / "sessions" / "session-1.md").exists()


def test_migrate_moves_orphans(tmp_path: Path, monkeypatch) -> None:
    vault, main, _wt = _make_repo_with_worktree_briefings(tmp_path)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=False, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    moved = vault / "bots" / "myproj" / "briefings" / "sessions" / "session-1.md"
    assert moved.exists()
    assert moved.read_text(encoding="utf-8") == "orphan briefing"
    # Source removed.
    assert not (vault / "bots" / "myproj-wt-feature-x" / "briefings" / "sessions" / "session-1.md").exists()


def test_migrate_skips_collisions(tmp_path: Path, capsys, monkeypatch) -> None:
    """If a target file with the same name already exists in canonical dir, skip + warn."""
    vault, main, _wt = _make_repo_with_worktree_briefings(tmp_path)
    canonical_dir = vault / "bots" / "myproj" / "briefings" / "sessions"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "session-1.md").write_text("existing canonical", encoding="utf-8")

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=False, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "collision" in out.lower() or "skipped" in out.lower()
    # Original orphan stays in place.
    assert (vault / "bots" / "myproj-wt-feature-x" / "briefings" / "sessions" / "session-1.md").exists()
    # Canonical file untouched.
    assert (canonical_dir / "session-1.md").read_text(encoding="utf-8") == "existing canonical"


def test_migrate_noop_when_nothing_to_move(tmp_path: Path, capsys, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    main = tmp_path / "myproj"
    main.mkdir()
    (main / ".git").mkdir()
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=False, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "nothing to migrate" in out.lower()


def _bare_repo_vault(tmp_path: Path, *names: str) -> tuple[Path, Path]:
    """A vault holding one briefing under each ``bots/<name>/``; repo ``myproj``."""
    vault = tmp_path / "vault"
    vault.mkdir()
    main = tmp_path / "myproj"
    main.mkdir()
    (main / ".git").mkdir()
    for name in names:
        sessions = vault / "bots" / name / "briefings" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / f"{name}-1.md").write_text(f"{name} briefing", encoding="utf-8")
    return vault, main


def _sessions(vault: Path, name: str) -> Path:
    return vault / "bots" / name / "briefings" / "sessions"


def test_migrate_never_moves_a_sibling_checkout(tmp_path: Path, capsys, monkeypatch) -> None:
    """``myproj-experimental`` shares the prefix but is its own checkout (#802).

    Until #802 the pick was ``startswith("myproj-")``, which moved it.
    """
    vault, main = _bare_repo_vault(tmp_path, "myproj-experimental")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=False, repos=[str(main)])
    assert cmd_mod.cmd_migrate_worktree_briefings(args) == 0
    out = capsys.readouterr().out
    assert (_sessions(vault, "myproj-experimental") / "myproj-experimental-1.md").exists()
    assert not (_sessions(vault, "myproj") / "myproj-experimental-1.md").exists()
    assert "nothing to migrate" in out.lower()


def test_migrate_dry_run_lists_moves_and_skipped_siblings(tmp_path: Path, capsys, monkeypatch) -> None:
    vault, main = _bare_repo_vault(tmp_path, "myproj-src", "myproj-wt-12", "myproj-src-wt-3")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=True, repos=[str(main)])
    assert cmd_mod.cmd_migrate_worktree_briefings(args) == 0
    lines = capsys.readouterr().out.splitlines()
    moves = [line for line in lines if line.startswith("would move")]
    skips = [line for line in lines if line.startswith("skipped")]
    assert len(moves) == 1 and "myproj-wt-12-1.md" in moves[0]
    assert len(skips) == 2
    assert any(str(vault / "bots" / "myproj-src") in s and "sibling checkout" in s for s in skips)
    # A worktree of the sibling is the sibling's, not myproj's.
    assert any("myproj-src-wt-3" in s and "'myproj-src'" in s for s in skips)


def test_migrate_moves_wt_dirs_including_nested(tmp_path: Path, monkeypatch) -> None:
    """``<repo>-wt-a-wt-b`` — a child cut from a child — still goes to ``<repo>``."""
    vault, main = _bare_repo_vault(tmp_path, "myproj-wt-7", "myproj-wt-r6-wt-41", "myproj-src")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=False, repos=[str(main)])
    assert cmd_mod.cmd_migrate_worktree_briefings(args) == 0
    canonical = _sessions(vault, "myproj")
    assert sorted(p.name for p in canonical.glob("*.md")) == [
        "myproj-wt-7-1.md",
        "myproj-wt-r6-wt-41-1.md",
    ]
    assert not list(_sessions(vault, "myproj-wt-7").glob("*.md"))
    assert not list(_sessions(vault, "myproj-wt-r6-wt-41").glob("*.md"))
    assert (_sessions(vault, "myproj-src") / "myproj-src-1.md").exists()


# --- #813: the doctor's hint runs as printed --------------------------------

import shlex  # noqa: E402

import pytest  # noqa: E402

from mnemo import cli  # noqa: E402
from mnemo.cli.commands.doctor_checks import orphan_worktree_briefings as check_mod  # noqa: E402


def _printed_command(vault: Path) -> list[str]:
    """The argv after ``mnemo`` in the one ``Run:`` hint the doctor prints."""
    findings = check_mod.check_orphan_worktree_briefings(vault)
    assert findings is not None and len(findings) == 1
    _, sep, command = findings[0].partition("Run: ")
    assert sep, findings[0]
    argv = shlex.split(command)
    assert argv[0] == "mnemo"
    return argv[1:]


def test_the_doctor_hint_previews_then_performs_the_flagged_move(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    """Printed, run verbatim: a preview of the flagged dir; then the move; then silence."""
    vault, _main = _bare_repo_vault(tmp_path, "myproj", "myproj-wt-12", "myproj-src")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)

    argv = _printed_command(vault)
    assert "<" not in " ".join(argv), "nothing is left to fill in"
    assert "--dry-run" in argv

    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    moves = [line for line in out.splitlines() if line.startswith("would move")]
    assert len(moves) == 1 and "myproj-wt-12-1.md" in moves[0]
    assert (_sessions(vault, "myproj-wt-12") / "myproj-wt-12-1.md").exists()

    assert cli.main([a for a in argv if a != "--dry-run"]) == 0
    assert (_sessions(vault, "myproj") / "myproj-wt-12-1.md").exists()
    assert not list(_sessions(vault, "myproj-wt-12").glob("*.md"))
    # The #802 guard: a sibling sharing the prefix stays put.
    assert (_sessions(vault, "myproj-src") / "myproj-src-1.md").exists()

    assert check_mod.check_orphan_worktree_briefings(vault) is None


def test_agents_never_moves_a_sibling_checkout(tmp_path: Path, capsys, monkeypatch) -> None:
    vault, _main = _bare_repo_vault(tmp_path, "myproj", "myproj-src", "myproj-src-wt-3")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    assert cli.main(["migrate-worktree-briefings", "--agents", "myproj"]) == 0
    out = capsys.readouterr().out
    assert "nothing to migrate" in out.lower()
    assert (_sessions(vault, "myproj-src") / "myproj-src-1.md").exists()
    assert (_sessions(vault, "myproj-src-wt-3") / "myproj-src-wt-3-1.md").exists()


def test_agents_and_repos_naming_one_agent_list_each_move_once(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    vault, main = _bare_repo_vault(tmp_path, "myproj-wt-12")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    rc = cli.main([
        "migrate-worktree-briefings", "--repos", str(main), "--agents", "myproj", "--dry-run",
    ])
    assert rc == 0
    moves = [line for line in capsys.readouterr().out.splitlines() if line.startswith("would move")]
    assert len(moves) == 1


@pytest.mark.parametrize("argv", [
    ["migrate-worktree-briefings"],
    ["migrate-worktree-briefings", "--dry-run"],
])
def test_called_without_a_target_is_a_usage_error(
    argv: list[str], tmp_path: Path, capsys, monkeypatch,
) -> None:
    """``--dry-run`` alone is what the maintainer ran; "nothing ran" must not exit 0."""
    vault, _main = _bare_repo_vault(tmp_path, "myproj", "myproj-wt-12")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert "--agents" in err
    assert (_sessions(vault, "myproj-wt-12") / "myproj-wt-12-1.md").exists()


@pytest.mark.parametrize("name", ["..", ".", "bots/myproj", str(Path("a") / "b")])
def test_agents_refuses_a_path(name: str, tmp_path: Path, capsys, monkeypatch) -> None:
    vault, _main = _bare_repo_vault(tmp_path, "myproj-wt-12")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    assert cli.main(["migrate-worktree-briefings", "--agents", name]) == 2
    assert "agent name" in capsys.readouterr().err
