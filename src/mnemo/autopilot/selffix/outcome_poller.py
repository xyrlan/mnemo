"""Autopilot Tier 1 — Outcome poller.

Polls closed self-fix PRs via ``gh pr list`` and feeds outcomes back to
:mod:`mnemo.autopilot.core.pr_budget`.

Run daily via ``autopilot.tier1.poll-outcomes`` cron job.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mnemo.autopilot.core import pr_budget
from mnemo.autopilot.core.labels import SELF_FIX_LABEL

# Map GitHub GraphQL PR states to our internal outcome strings
_STATE_MAP = {
    "MERGED": "merged",
    "CLOSED": "closed",
}


def poll_outcomes(*, vault_root: Path) -> int:
    """Query closed self-fix PRs and record their outcomes.

    Returns the number of outcomes recorded.
    """
    try:
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--label", SELF_FIX_LABEL,
                "--state", "closed",
                "--json", "number,state",
                "--limit", "50",
            ],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return 0

    if result.returncode != 0:
        return 0

    try:
        prs = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return 0

    count = 0
    for pr in prs:
        number = pr.get("number")
        state = pr.get("state", "")
        outcome = _STATE_MAP.get(state)
        if outcome is None or number is None:
            continue
        pr_budget.record_outcome(
            vault_root=vault_root, pr_number=int(number), outcome=outcome
        )
        count += 1

    return count
