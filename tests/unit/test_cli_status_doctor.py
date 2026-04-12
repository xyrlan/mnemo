from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.core import errors


def test_status_clean(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    rc = cli.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "vault" in out.lower()
    assert "hooks" in out.lower()
    assert "circuit breaker" in out.lower()
    assert "closed" in out.lower() or "ok" in out.lower()


def test_status_reports_open_breaker(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    vault = tmp_home / "v"
    for i in range(15):
        try:
            raise ValueError(f"e{i}")
        except ValueError as e:
            errors.log_error(vault, "test", e)
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "open" in out.lower()


def test_doctor_runs_preflight_and_reports(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "preflight" in out.lower() or "diagnostic" in out.lower()


def test_fix_resets_breaker(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    vault = tmp_home / "v"
    for i in range(15):
        try:
            raise ValueError("e")
        except ValueError as e:
            errors.log_error(vault, "test", e)
    assert not errors.should_run(vault)
    cli.main(["fix"])
    assert errors.should_run(vault) is True


def test_open_returns_zero_when_no_opener(tmp_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    monkeypatch.setattr(cli, "_run_open", lambda path: None)
    rc = cli.main(["open"])
    assert rc == 0
