"""Tests for scheduled job registration — T12."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.autopilot.tuner import register_tune_jobs
from mnemo.autopilot.core.dispatcher import list_autopilot_jobs


class TestRegisterTuneJobs:
    def test_registers_two_jobs(self, tmp_path: Path):
        register_tune_jobs(tmp_path)
        jobs = list_autopilot_jobs(vault_root=tmp_path)
        names = {j.name for j in jobs}
        assert "autopilot.tier2.bm25" in names
        assert "autopilot.tier2.reflex" in names

    def test_job_crons(self, tmp_path: Path):
        register_tune_jobs(tmp_path)
        jobs = {j.name: j for j in list_autopilot_jobs(vault_root=tmp_path)}
        assert jobs["autopilot.tier2.bm25"].cron == "0 13 * * 0"
        assert jobs["autopilot.tier2.reflex"].cron == "0 14 * * 0"

    def test_job_commands(self, tmp_path: Path):
        register_tune_jobs(tmp_path)
        jobs = {j.name: j for j in list_autopilot_jobs(vault_root=tmp_path)}
        assert "bm25" in jobs["autopilot.tier2.bm25"].command
        assert "reflex" in jobs["autopilot.tier2.reflex"].command

    def test_idempotent(self, tmp_path: Path):
        register_tune_jobs(tmp_path)
        register_tune_jobs(tmp_path)
        jobs = list_autopilot_jobs(vault_root=tmp_path)
        names = [j.name for j in jobs]
        # No duplicates
        assert len(names) == len(set(names))
        assert len([n for n in names if "tier2" in n]) == 2
