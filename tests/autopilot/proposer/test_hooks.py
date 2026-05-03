"""Tests for autopilot/proposer/_hooks.py"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mnemo.autopilot.proposer._hooks import run_preempt_sync


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


def test_register_eos_sweep_job_is_gone():
    """The fake-cron registration shim must not be re-introduced.

    Tier 3 EoS now runs from the SessionEnd hook directly; there is no
    cron job to register.
    """
    from mnemo.autopilot.proposer import _hooks
    assert not hasattr(_hooks, "register_eos_sweep_job")
