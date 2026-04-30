"""Hook glue for Tier 3 proposer: SessionStart + SessionEnd integration."""
from __future__ import annotations

from pathlib import Path
from typing import List

# Module-level imports so patch() targets are reachable
from mnemo.autopilot.proposer.preempt import predict_next_action, write_preempt_cache
from mnemo.autopilot.core.dispatcher import schedule_autopilot_job


def run_preempt_sync(
    *,
    vault_root: Path,
    project: str,
    cwd: str,
) -> List[str]:
    """Run prediction + write preempt cache synchronously; return predicted slugs.

    Swallows all exceptions so hook callers are never blocked.
    """
    try:
        cwd_path = Path(cwd)
        slugs = predict_next_action(
            vault_root=vault_root,
            project=project,
            cwd=cwd_path,
        )
        write_preempt_cache(
            vault_root=vault_root,
            project=project,
            slugs=slugs,
            cwd=cwd_path,
        )
        return slugs
    except Exception:
        return []


def register_eos_sweep_job(vault_root: Path) -> None:
    """Register the eos-sweep cron job in autopilot-jobs.json.

    Called from ``mnemo autopilot on`` so the sweep is recorded even when
    the hook hasn't fired yet.
    """
    try:
        schedule_autopilot_job(
            vault_root=vault_root,
            name="autopilot.tier3.eos-sweep",
            cron="*/30 * * * *",
            command="mnemo autopilot propose --session-id sweep",
        )
    except Exception:
        pass
