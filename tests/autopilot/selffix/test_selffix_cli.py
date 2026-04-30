"""Tests for `mnemo autopilot self-fix` CLI subcommands."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mnemo.cli.runtime import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(monkeypatch, tmp_path: Path, *args: str, capsys) -> tuple:
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path, raising=False)
    rc = main([*args])
    out, _err = capsys.readouterr()
    return rc, out


def _setup_vault(tmp_path: Path) -> None:
    (tmp_path / ".mnemo").mkdir(exist_ok=True)
    (tmp_path / ".mnemo" / "autopilot.json").write_text(
        json.dumps({
            "schema_version": 1, "state": "on", "paused_until": None,
            "last_changed_at": None, "last_changed_by": None,
        })
    )


# ---------------------------------------------------------------------------
# self-fix doctor --dry-run
# ---------------------------------------------------------------------------


def test_selffix_doctor_dry_run_no_pr(monkeypatch, tmp_path: Path, capsys) -> None:
    _setup_vault(tmp_path)
    with patch("mnemo.autopilot.selffix.doctor_fixer.detect_fixable", return_value=[]), \
         patch("mnemo.autopilot.selffix.doctor_fixer.open_doctor_fix_pr") as mock_pr:
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "self-fix", "doctor", "--dry-run",
            capsys=capsys,
        )
    mock_pr.assert_not_called()
    assert rc == 0


def test_selffix_doctor_dry_run_lists_warnings(monkeypatch, tmp_path: Path, capsys) -> None:
    from mnemo.autopilot.selffix.doctor_fixer import DoctorWarning

    _setup_vault(tmp_path)
    warnings = [
        DoctorWarning(
            kind="source_path_missing",
            rule_path=tmp_path / "shared" / "feedback" / "rule.md",
            detail="briefings/gone.md",
        )
    ]
    with patch("mnemo.autopilot.selffix.doctor_fixer.detect_fixable", return_value=warnings):
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "self-fix", "doctor", "--dry-run",
            capsys=capsys,
        )
    assert rc == 0
    assert "source_path_missing" in out or "rule.md" in out or "1" in out


def test_selffix_doctor_without_dry_run_calls_open_pr(monkeypatch, tmp_path: Path, capsys) -> None:
    from mnemo.autopilot.selffix.doctor_fixer import DoctorWarning

    _setup_vault(tmp_path)
    warnings = [
        DoctorWarning(
            kind="source_path_missing",
            rule_path=tmp_path / "shared" / "feedback" / "rule.md",
            detail="briefings/gone.md",
        )
    ]
    with patch("mnemo.autopilot.selffix.doctor_fixer.detect_fixable", return_value=warnings), \
         patch("mnemo.autopilot.selffix.doctor_fixer.open_doctor_fix_pr", return_value=None) as mock_pr:
        rc, _out = _run(
            monkeypatch, tmp_path,
            "autopilot", "self-fix", "doctor",
            capsys=capsys,
        )
    mock_pr.assert_called_once()


# ---------------------------------------------------------------------------
# self-fix sweep --dry-run
# ---------------------------------------------------------------------------


def test_selffix_sweep_dry_run_no_pr(monkeypatch, tmp_path: Path, capsys) -> None:
    _setup_vault(tmp_path)
    with patch("mnemo.autopilot.selffix.dead_rule_sweep.detect_dead_rules", return_value=[]), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep.open_dead_rule_pr") as mock_pr:
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "self-fix", "sweep", "--dry-run",
            capsys=capsys,
        )
    mock_pr.assert_not_called()
    assert rc == 0


def test_selffix_sweep_dry_run_lists_dead_rules(monkeypatch, tmp_path: Path, capsys) -> None:
    from mnemo.autopilot.selffix.dead_rule_sweep import DeadRule

    _setup_vault(tmp_path)
    dead = [DeadRule(rule_path=tmp_path / "shared" / "feedback" / "old.md", slug="old", last_seen_days=100)]
    with patch("mnemo.autopilot.selffix.dead_rule_sweep.detect_dead_rules", return_value=dead):
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "self-fix", "sweep", "--dry-run",
            capsys=capsys,
        )
    assert rc == 0
    assert "old" in out or "1" in out


# ---------------------------------------------------------------------------
# self-fix telemetry --dry-run
# ---------------------------------------------------------------------------


def test_selffix_telemetry_dry_run_no_pr(monkeypatch, tmp_path: Path, capsys) -> None:
    _setup_vault(tmp_path)
    with patch("mnemo.autopilot.selffix.telemetry_doctor.scan_telemetry", return_value=[]), \
         patch("mnemo.autopilot.selffix.telemetry_doctor.open_telemetry_fix_pr") as mock_pr:
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "self-fix", "telemetry", "--dry-run",
            capsys=capsys,
        )
    mock_pr.assert_not_called()
    assert rc == 0


def test_selffix_telemetry_dry_run_lists_anomalies(monkeypatch, tmp_path: Path, capsys) -> None:
    from mnemo.autopilot.selffix.telemetry_doctor import TelemetryAnomaly

    _setup_vault(tmp_path)
    anomalies = [TelemetryAnomaly(kind="cost_usd_always_zero", detail="x", affected_count=10)]
    with patch("mnemo.autopilot.selffix.telemetry_doctor.scan_telemetry", return_value=anomalies):
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "self-fix", "telemetry", "--dry-run",
            capsys=capsys,
        )
    assert rc == 0
    assert "cost_usd_always_zero" in out or "10" in out or "1" in out


# ---------------------------------------------------------------------------
# Global --dry-run flag runs all three
# ---------------------------------------------------------------------------


def test_selffix_global_dry_run_runs_all_three(monkeypatch, tmp_path: Path, capsys) -> None:
    _setup_vault(tmp_path)
    with patch("mnemo.autopilot.selffix.doctor_fixer.detect_fixable", return_value=[]) as mock_d, \
         patch("mnemo.autopilot.selffix.dead_rule_sweep.detect_dead_rules", return_value=[]) as mock_s, \
         patch("mnemo.autopilot.selffix.telemetry_doctor.scan_telemetry", return_value=[]) as mock_t:
        rc, _out = _run(
            monkeypatch, tmp_path,
            "autopilot", "self-fix", "--dry-run",
            capsys=capsys,
        )
    mock_d.assert_called_once()
    mock_s.assert_called_once()
    mock_t.assert_called_once()
    assert rc == 0


# ---------------------------------------------------------------------------
# Missing subcommand
# ---------------------------------------------------------------------------


def test_selffix_no_subcommand_exits_nonzero(monkeypatch, tmp_path: Path, capsys) -> None:
    _setup_vault(tmp_path)
    rc, out = _run(
        monkeypatch, tmp_path,
        "autopilot", "self-fix",
        capsys=capsys,
    )
    # Should print usage and return 2
    assert rc == 2
