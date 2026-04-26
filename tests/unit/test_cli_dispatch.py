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
