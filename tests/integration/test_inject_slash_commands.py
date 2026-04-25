from pathlib import Path

from mnemo.install import settings as inj


EXPECTED_NAMES = {
    "init", "init-project", "status", "doctor",
    "open", "fix", "uninstall", "uninstall-project", "help",
}


def test_inject_slash_commands_writes_all_nine_commands(tmp_path: Path):
    commands_dir = tmp_path / "commands"
    inj.inject_slash_commands(commands_dir)

    files = {p.stem for p in commands_dir.glob("*.md")}
    assert EXPECTED_NAMES.issubset(files)


def test_inject_slash_commands_idempotent(tmp_path: Path):
    commands_dir = tmp_path / "commands"
    inj.inject_slash_commands(commands_dir)
    inj.inject_slash_commands(commands_dir)

    files = list(commands_dir.glob("*.md"))
    # Each name appears exactly once — no duplicates from a second run.
    stems = [p.stem for p in files]
    assert len(stems) == len(set(stems))
    assert set(stems) == EXPECTED_NAMES


def test_inject_slash_commands_writes_bash_injection_body(tmp_path: Path):
    commands_dir = tmp_path / "commands"
    inj.inject_slash_commands(commands_dir)

    init_md = (commands_dir / "init.md").read_text()
    # mnemo marker so uninject can identify mnemo-owned files
    assert inj.SLASH_COMMAND_TAG in init_md
    # Body invokes mnemo via bash injection
    assert "!`python3 -m mnemo init`" in init_md
    # init-project carries the --project flag
    init_project_md = (commands_dir / "init-project.md").read_text()
    assert "!`python3 -m mnemo init --project`" in init_project_md


def test_uninject_slash_commands_strips_only_mnemo(tmp_path: Path):
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    # Pre-existing third-party command file (NOT mnemo's): must be preserved.
    (commands_dir / "other-cmd.md").write_text("---\ndescription: third-party\n---\n\nDo a thing.\n")

    inj.inject_slash_commands(commands_dir)
    inj.uninject_slash_commands(commands_dir)

    remaining = {p.stem for p in commands_dir.glob("*.md")}
    assert remaining == {"other-cmd"}


def test_uninject_slash_commands_handles_missing_dir(tmp_path: Path):
    # Should not raise even if commands_dir doesn't exist (already cleaned up).
    inj.uninject_slash_commands(tmp_path / "nonexistent")
