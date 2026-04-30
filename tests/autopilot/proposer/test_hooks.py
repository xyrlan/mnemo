"""Tests for autopilot/proposer/_hooks.py"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mnemo.autopilot.proposer._hooks import register_eos_sweep_job, run_preempt_sync


def test_run_preempt_sync_returns_slugs(tmp_path: Path):
    with patch(
        "mnemo.autopilot.proposer._hooks.predict_next_action",
        return_value=["slug-a", "slug-b"],
    ), patch("mnemo.autopilot.proposer._hooks.write_preempt_cache") as mock_write:
        slugs = run_preempt_sync(vault_root=tmp_path, project="p", cwd=str(tmp_path))
    assert slugs == ["slug-a", "slug-b"]
    mock_write.assert_called_once()


def test_run_preempt_sync_swallows_exception(tmp_path: Path):
    with patch(
        "mnemo.autopilot.proposer._hooks.predict_next_action",
        side_effect=RuntimeError("boom"),
    ):
        slugs = run_preempt_sync(vault_root=tmp_path, project="p", cwd=str(tmp_path))
    assert slugs == []


def test_register_eos_sweep_job_writes_job(tmp_path: Path):
    register_eos_sweep_job(tmp_path)
    jobs_path = tmp_path / ".mnemo" / "autopilot-jobs.json"
    assert jobs_path.exists()
    data = json.loads(jobs_path.read_text())
    assert "autopilot.tier3.eos-sweep" in data["jobs"]
    job = data["jobs"]["autopilot.tier3.eos-sweep"]
    assert job["cron"] == "*/30 * * * *"
    assert "propose" in job["command"]


def test_register_eos_sweep_job_swallows_exception(tmp_path: Path):
    """Must not raise even when dispatcher fails."""
    with patch(
        "mnemo.autopilot.proposer._hooks.schedule_autopilot_job",
        side_effect=RuntimeError("fail"),
    ):
        # Should not raise
        register_eos_sweep_job(tmp_path)
