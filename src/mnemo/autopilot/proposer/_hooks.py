"""Hook glue for Tier 3 proposer: SessionStart + SessionEnd integration."""
from __future__ import annotations

from pathlib import Path
from typing import List

# Module-level imports so patch() targets are reachable
from mnemo.autopilot.proposer.preempt import predict_next_action, write_preempt_cache


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


