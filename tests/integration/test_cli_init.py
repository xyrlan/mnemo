from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from mnemo import cli


def test_init_yes_creates_vault_and_injects(tmp_home: Path, capsys: pytest.CaptureFixture):
    rc = cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault")])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    vault = tmp_home / "vault"
    assert (vault / "HOME.md").exists()
    assert (vault / "mnemo.config.json").exists()
    settings_path = tmp_home / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "SessionStart" in data["hooks"]


def test_init_idempotent(tmp_home: Path):
    args = ["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"]
    assert cli.main(args) == 0
    assert cli.main(args) == 0


def test_init_no_mirror_skips_claude_sync(tmp_home: Path):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    # No bots/<agent>/memory dirs should have been created from a sync.
    bots = tmp_home / "v" / "bots"
    assert bots.exists()
    assert not any(bots.iterdir())


def test_init_quiet_suppresses_stdout(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    out = capsys.readouterr().out
    assert out.strip() == ""


def test_init_interactive_uses_default_when_blank(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    answers = iter(["", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc = cli.main(["init"])
    assert rc == 0
    default_vault = tmp_home / "mnemo"
    assert default_vault.exists()


def test_init_interactive_aborts_on_no(tmp_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    answers = iter([str(tmp_home / "v"), "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc = cli.main(["init"])
    assert rc != 0
    captured = capsys.readouterr()
    assert "abort" in (captured.out + captured.err).lower()
