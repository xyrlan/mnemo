"""Tests for Tier 1 scheduled job registration."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.cli.runtime import main


def _run(monkeypatch, tmp_path: Path, *args: str, capsys) -> tuple:
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path, raising=False)
    rc = main([*args])
    out, _err = capsys.readouterr()
    return rc, out


def test_autopilot_on_registers_selffix_jobs(monkeypatch, tmp_path: Path, capsys) -> None:
    """After ``autopilot on``, three self-fix jobs must be present in jobs file."""
    rc, _ = _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    assert rc == 0

    jobs_path = tmp_path / ".mnemo" / "autopilot-jobs.json"
    assert jobs_path.exists(), "autopilot-jobs.json should exist after 'autopilot on'"
    data = json.loads(jobs_path.read_text())
    jobs = data.get("jobs", {})

    assert "autopilot.tier1.doctor" in jobs
    assert "autopilot.tier1.sweep" in jobs
    assert "autopilot.tier1.telemetry" in jobs


def test_autopilot_on_registers_poll_outcomes_job(monkeypatch, tmp_path: Path, capsys) -> None:
    rc, _ = _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    assert rc == 0

    jobs_path = tmp_path / ".mnemo" / "autopilot-jobs.json"
    data = json.loads(jobs_path.read_text())
    jobs = data.get("jobs", {})
    assert "autopilot.tier1.poll-outcomes" in jobs


def test_selffix_jobs_have_correct_crons(monkeypatch, tmp_path: Path, capsys) -> None:
    _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    data = json.loads((tmp_path / ".mnemo" / "autopilot-jobs.json").read_text())
    jobs = data["jobs"]

    assert jobs["autopilot.tier1.doctor"]["cron"] == "0 10 * * 1"   # weekly Mon
    assert jobs["autopilot.tier1.sweep"]["cron"] == "0 11 1 * *"    # monthly 1st
    assert jobs["autopilot.tier1.telemetry"]["cron"] == "0 12 * * 0"  # weekly Sun


def test_autopilot_off_removes_selffix_jobs(monkeypatch, tmp_path: Path, capsys) -> None:
    _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    _run(monkeypatch, tmp_path, "autopilot", "off", capsys=capsys)

    jobs_path = tmp_path / ".mnemo" / "autopilot-jobs.json"
    data = json.loads(jobs_path.read_text())
    jobs = data.get("jobs", {})
    assert "autopilot.tier1.doctor" not in jobs
    assert "autopilot.tier1.sweep" not in jobs
    assert "autopilot.tier1.telemetry" not in jobs
