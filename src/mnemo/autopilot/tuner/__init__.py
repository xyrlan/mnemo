"""Autopilot Tier 2 — Self-Tuner.

Tier 2 cadence is enforced by ``mnemo.autopilot.core.scheduler.run_due_jobs``
(hook-driven), not by an OS cron. This package only exposes the tuner
implementations themselves.
"""
from __future__ import annotations

__all__: list[str] = []
