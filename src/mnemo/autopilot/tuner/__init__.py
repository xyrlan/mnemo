"""Autopilot Tier 2 — Self-Tuner.

Exports:
    register_tune_jobs: Register weekly BM25 and reflex calibration jobs
                        with the autopilot dispatcher.
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["register_tune_jobs"]


def register_tune_jobs(vault_root: Path) -> None:
    """Register Tier 2 tuner jobs with the autopilot dispatcher.

    Jobs registered:
    - autopilot.tier2.bm25   — weekly Sunday 13:00 UTC
    - autopilot.tier2.reflex — weekly Sunday 14:00 UTC

    Idempotent: calling multiple times does not duplicate entries.
    """
    from mnemo.autopilot.core.dispatcher import schedule_autopilot_job

    schedule_autopilot_job(
        vault_root=vault_root,
        name="autopilot.tier2.bm25",
        cron="0 13 * * 0",
        command="mnemo autopilot tune bm25",
    )
    schedule_autopilot_job(
        vault_root=vault_root,
        name="autopilot.tier2.reflex",
        cron="0 14 * * 0",
        command="mnemo autopilot tune reflex",
    )
