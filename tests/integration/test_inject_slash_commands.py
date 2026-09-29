from pathlib import Path

from mnemo.install import settings as inj


EXPECTED_NAMES = {
    "init", "init-project", "uninstall", "uninstall-project",
    "status", "why", "doctor", "learn", "dispatch", "help",
}


def test_inject_slash_commands_writes_all_commands(tmp_path: Path):
    commands_dir = tmp_path / "commands"
    inj.inject_slash_commands(commands_dir)

    files = {p.stem for p in commands_dir.glob("*.md")}
    assert files == EXPECTED_NAMES


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

    init_md = (commands_dir / "init.md").read_text(encoding="utf-8")
    # mnemo marker so uninject can identify mnemo-owned files
    assert inj.SLASH_COMMAND_TAG in init_md
    # Body invokes mnemo via bash injection, through the mnemo that is actually
    # installed rather than a bare `python3` that may resolve elsewhere.
    from mnemo._selfexec import self_command
    assert f"!`{self_command('init')}`" in init_md
    assert init_md.rstrip().endswith("-m mnemo init`")
    # init-project carries the --project flag
    init_project_md = (commands_dir / "init-project.md").read_text(encoding="utf-8")
    assert f"!`{self_command('init', '--project')}`" in init_project_md


def test_uninject_slash_commands_strips_only_mnemo(tmp_path: Path):
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    # Pre-existing third-party command file (NOT mnemo's): must be preserved.
    (commands_dir / "other-cmd.md").write_text("---\ndescription: third-party\n---\n\nDo a thing.\n", encoding="utf-8")

    inj.inject_slash_commands(commands_dir)
    inj.uninject_slash_commands(commands_dir)

    remaining = {p.stem for p in commands_dir.glob("*.md")}
    assert remaining == {"other-cmd"}


def test_uninject_slash_commands_handles_missing_dir(tmp_path: Path):
    # Should not raise even if commands_dir doesn't exist (already cleaned up).
    inj.uninject_slash_commands(tmp_path / "nonexistent")


# --- #553: commands mnemo stopped shipping are removed, a user's never ------

# The shape an old `mnemo init` left behind: the tag above the frontmatter, so
# Claude Code parsed none of it and listed the command, model-invocable.
_LEGACY_FIX = (
    "<!-- mnemo:slash-command -->\n---\ndescription: reset circuit breaker\n"
    "allowed-tools: Bash\ndisable-model-invocation: true\n---\n\n!`mnemo fix`\n"
)


def test_inject_removes_mnemo_commands_no_longer_shipped(tmp_path: Path):
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    (commands_dir / "fix.md").write_text(_LEGACY_FIX, encoding="utf-8")
    (commands_dir / "open.md").write_text(
        "---\ndescription: open\n---\n<!-- mnemo:slash-command -->\n\n!`mnemo open`\n",
        encoding="utf-8",
    )

    inj.inject_slash_commands(commands_dir)

    assert {p.stem for p in commands_dir.glob("*.md")} == EXPECTED_NAMES


def test_inject_never_removes_a_users_own_command(tmp_path: Path):
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    mine = "---\ndescription: mine\n---\n\nDo a thing.\n"
    # Mentions the tag in prose, never on a line of its own: still the user's.
    quoting = "---\ndescription: notes\n---\n\nmnemo tags its files `<!-- mnemo:slash-command -->`.\n"
    (commands_dir / "fix.md").write_text(mine, encoding="utf-8")
    (commands_dir / "notes.md").write_text(quoting, encoding="utf-8")
    (commands_dir / "status.md").write_text(mine, encoding="utf-8")

    inj.inject_slash_commands(commands_dir)

    assert (commands_dir / "fix.md").read_text(encoding="utf-8") == mine
    assert (commands_dir / "notes.md").read_text(encoding="utf-8") == quoting
    # A shipped name the user owns is left alone, as before.
    assert (commands_dir / "status.md").read_text(encoding="utf-8") == mine


def test_prune_reports_what_it_removed(tmp_path: Path):
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    (commands_dir / "fix.md").write_text(_LEGACY_FIX, encoding="utf-8")
    assert inj.prune_slash_commands(commands_dir) == ["fix"]
    assert inj.prune_slash_commands(commands_dir) == []
    assert inj.prune_slash_commands(tmp_path / "missing") == []


def test_every_command_mnemo_writes_opens_with_its_frontmatter(tmp_path: Path):
    """The tag sits below the frontmatter, never above it (#233, #553)."""
    commands_dir = tmp_path / "commands"
    inj.inject_slash_commands(commands_dir)
    for path in commands_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n"), path.name
        close = text.index("\n---\n", 3) + len("\n---\n")
        assert text[close:].startswith(inj.SLASH_COMMAND_TAG + "\n"), path.name
