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
