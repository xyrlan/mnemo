from __future__ import annotations

import pytest

from mnemo import cli


def test_help_lists_all_commands(capsys: pytest.CaptureFixture):
    rc = cli.main(["help"])
    captured = capsys.readouterr()
    assert rc == 0
    for cmd in ("init", "status", "doctor", "open", "promote", "compile", "fix", "uninstall", "help"):
        assert cmd in captured.out


def test_unknown_command_returns_nonzero(capsys: pytest.CaptureFixture):
    rc = cli.main(["bogus-cmd"])
    captured = capsys.readouterr()
    assert rc != 0


def test_no_args_shows_help(capsys: pytest.CaptureFixture):
    rc = cli.main([])
    captured = capsys.readouterr()
    assert rc == 0
    assert "init" in captured.out
