from __future__ import annotations

import pytest

from mnemo import cli


def test_help_lists_all_commands(capsys: pytest.CaptureFixture):
    rc = cli.main(["help"])
    captured = capsys.readouterr()
    assert rc == 0
    for cmd in ("init", "status", "doctor", "open", "fix", "uninstall", "help"):
        assert cmd in captured.out
    # v0.4: promote/compile were removed — dashboard auto-regenerates via extraction
    assert "promote" not in captured.out
    assert " compile" not in captured.out


def test_help_hides_advanced_commands_by_default(capsys: pytest.CaptureFixture):
    rc = cli.main(["help"])
    captured = capsys.readouterr()
    assert rc == 0
    # Advanced/maintenance commands are hidden unless --all is passed
    for advanced in ("telemetry", "recall", "dedup-rules", "list-enforced", "regen-graph-edges"):
        assert advanced not in captured.out
    # The footer points users at --all
    assert "mnemo help --all" in captured.out


def test_help_all_shows_advanced_commands(capsys: pytest.CaptureFixture):
    rc = cli.main(["help", "--all"])
    captured = capsys.readouterr()
    assert rc == 0
    for advanced in ("telemetry", "recall", "dedup-rules", "list-enforced", "regen-graph-edges"):
        assert advanced in captured.out
    # Internal-only subparsers stay hidden even with --all
    for internal in ("mcp-server", "statusline-compose"):
        assert internal not in captured.out


def test_unknown_command_returns_nonzero(capsys: pytest.CaptureFixture):
    rc = cli.main(["bogus-cmd"])
    captured = capsys.readouterr()
    assert rc != 0


def test_no_args_shows_help(capsys: pytest.CaptureFixture):
    rc = cli.main([])
    captured = capsys.readouterr()
    assert rc == 0
    assert "init" in captured.out


def test_version_flag_prints_version(capsys: pytest.CaptureFixture):
    rc = cli.main(["--version"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "mnemo" in captured.out


# --- #216: the contract format has to be reachable from the command --------


def test_contract_example_prints_a_parseable_contract(
    capsys: pytest.CaptureFixture, tmp_path
):
    """`--contract --example` emits a contract, not a description of one.

    The point of emitting it from the command that consumes it is that the
    output can be redirected to a file and dispatched. If what it prints
    would not parse, the example teaches a shape the command refuses.
    """
    from pathlib import Path

    from mnemo.core import contracts

    rc = cli.main(["dispatch", "--contract", "--example"])
    captured = capsys.readouterr()
    assert rc == 0

    target = Path(tmp_path) / "emitted.md"
    target.write_text(captured.out, encoding="utf-8")
    contract = contracts.parse_contract(target)
    assert contract.is_dispatchable
    assert len(contract.pieces) >= 2


def test_contract_example_needs_no_git_repository(
    capsys: pytest.CaptureFixture, monkeypatch
):
    """Printing the format is documentation, not dispatch.

    `cmd_dispatch` refuses outside a git repository because it has to branch
    from one. `--example` spawns nothing, so that refusal would block the one
    command someone runs *before* they have anything set up.
    """
    from mnemo.cli.commands import dispatch as cmd

    monkeypatch.setattr(cmd, "_repo_root", lambda: None)
    rc = cli.main(["dispatch", "--contract", "--example"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "feature:" in captured.out
    assert "not inside a git repository" not in captured.out


def test_contract_example_does_not_dispatch(capsys: pytest.CaptureFixture):
    """It prints and exits — no worktree, no child, no `gh` call."""
    from mnemo.core import dispatch as core

    def explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("--example must not dispatch anything")

    original = core.dispatch_contract
    core.dispatch_contract = explode
    try:
        rc = cli.main(["dispatch", "--contract", "--example"])
    finally:
        core.dispatch_contract = original
    assert rc == 0
    assert "## storage" in capsys.readouterr().out


def test_dispatch_help_points_at_the_skill(capsys: pytest.CaptureFixture):
    """`--contract PATH` alone never said what belongs in PATH (#216)."""
    rc = cli.main(["dispatch", "--help"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "--example" in captured.out
    # argparse wraps long help text, so match on the distinctive segment
    # rather than the whole path, which is split across two lines.
    assert "decomposing-for-dispatch" in captured.out


def test_help_skill_pointer_matches_the_module(capsys: pytest.CaptureFixture):
    """The parser spells the skill path as a literal; keep it honest.

    `parser.py` cannot import `mnemo.core.contracts` to build help text
    (every command pays that import), so the path is duplicated. This fails
    if the two ever disagree, which is the only thing the duplication risks.
    """
    from mnemo.core import contracts

    cli.main(["dispatch", "--help"])
    rendered = capsys.readouterr().out
    # Undo argparse's line wrapping before matching the full path.
    flattened = " ".join(rendered.split())
    assert contracts.SKILL in flattened


def test_unparseable_contract_refusal_reaches_the_user(
    capsys: pytest.CaptureFixture, tmp_path, monkeypatch
):
    """The teaching refusal has to survive the CLI's own error wrapping.

    `_dispatch_contract` catches ContractError and reprints it. A message
    that is multi-line and shaped like a template is exactly what that path
    has never carried, so pin that it arrives whole.
    """
    from pathlib import Path

    from mnemo.cli.commands import dispatch as cmd

    monkeypatch.setattr(cmd, "_repo_root", lambda: Path(tmp_path))
    target = Path(tmp_path) / "plan.md"
    target.write_text("# My Plan\n\nNotes.\n", encoding="utf-8")

    rc = cli.main(["dispatch", "--contract", str(target)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "not a contract" in captured.out
    assert "- **files:**" in captured.out
    assert "decomposing-for-dispatch" in captured.out


def test_example_works_without_the_contract_flag(capsys: pytest.CaptureFixture):
    """`mnemo dispatch --example` alone prints the same thing.

    There is only one thing `--example` could mean on this command, so
    requiring `--contract` alongside it would be pedantry at exactly the
    moment someone is trying to learn the format. Both spellings work, and
    the help text promises the looser one.
    """
    from mnemo.core import contracts

    rc = cli.main(["dispatch", "--example"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == contracts.EXAMPLE
