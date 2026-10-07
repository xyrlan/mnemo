"""Doctor check: Claude Code will delete transcripts mnemo learns from (#596)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.cli.commands import doctor
from mnemo.cli.commands.doctor_checks.transcript_retention import (
    _doctor_check_transcript_retention as check,
)


@pytest.fixture
def cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    return proj


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _user(home: Path, data: dict) -> None:
    _write(home / ".claude" / "settings.json", data)


def test_the_row_is_registered() -> None:
    assert ("transcript_retention", check) in doctor.DOCTOR_CHECKS


def test_unset_warns_with_the_fix(tmp_home: Path, cwd: Path, capsys) -> None:
    _user(tmp_home, {"theme": "dark"})

    assert check(tmp_home / "mnemo") is False

    out = capsys.readouterr().out
    assert "cleanupPeriodDays is unset, so Claude Code deletes transcripts after 30 days" in out
    assert 'add "cleanupPeriodDays": 365 to ~/.claude/settings.json' in out


def test_no_settings_file_warns(tmp_home: Path, cwd: Path, capsys) -> None:
    assert check(tmp_home / "mnemo") is False
    assert "is unset" in capsys.readouterr().out


def test_low_value_warns(tmp_home: Path, cwd: Path, capsys) -> None:
    _user(tmp_home, {"cleanupPeriodDays": 10})

    assert check(tmp_home / "mnemo") is False
    assert "cleanupPeriodDays is 10 in" in capsys.readouterr().out


def test_high_value_is_silent(tmp_home: Path, cwd: Path, capsys) -> None:
    _user(tmp_home, {"cleanupPeriodDays": 3650})

    assert check(tmp_home / "mnemo") is True
    assert capsys.readouterr().out == ""


def test_the_floor_itself_is_silent(tmp_home: Path, cwd: Path, capsys) -> None:
    _user(tmp_home, {"cleanupPeriodDays": 365})
    assert check(tmp_home / "mnemo") is True


def test_a_lower_project_value_wins_and_is_named(tmp_home: Path, cwd: Path, capsys) -> None:
    _user(tmp_home, {"cleanupPeriodDays": 3650})
    _write(cwd / ".claude" / "settings.local.json", {"cleanupPeriodDays": 7})

    assert check(tmp_home / "mnemo") is False
    out = capsys.readouterr().out
    assert "cleanupPeriodDays is 7 in" in out and "settings.local.json" in out
    assert "raise or remove the lower value" in out


def test_opt_out_silences_it(tmp_home: Path, cwd: Path, capsys) -> None:
    _write(tmp_home / "mnemo" / "mnemo.config.json", {"install": {"keepTranscriptsDays": 0}})

    assert check(tmp_home / "mnemo") is True
    assert capsys.readouterr().out == ""


def test_a_configured_floor_is_the_bar(tmp_home: Path, cwd: Path, capsys) -> None:
    _write(tmp_home / "mnemo" / "mnemo.config.json", {"install": {"keepTranscriptsDays": 3650}})
    _user(tmp_home, {"cleanupPeriodDays": 365})

    assert check(tmp_home / "mnemo") is False
    assert "floor 3650" in capsys.readouterr().out
