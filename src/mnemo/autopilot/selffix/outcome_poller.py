"""Autopilot Tier 1 — Outcome poller.

Polls closed self-fix PRs via ``gh pr list`` and closed self-fix issues via
``gh issue list``, feeding both back to :mod:`mnemo.autopilot.core.pr_budget`.

Issues matter because findings with no diff (telemetry anomalies) are filed as
issues, not PRs — without polling them their outcome never returns and the
autopilot cannot tell whether its findings land.

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

# A closed issue carries its verdict in stateReason: the finding was acted on
# (COMPLETED — same signal as a merged PR) or rejected (NOT_PLANNED).
_ISSUE_REASON_MAP = {
    "COMPLETED": "merged",
    "NOT_PLANNED": "closed",
}


def _gh_json(cmd: list) -> list:
    """Run a ``gh`` command expected to print a JSON array; [] on any failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return []
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def poll_outcomes(*, vault_root: Path) -> int:
    """Query closed self-fix PRs and issues and record their outcomes.

    Returns the number of outcomes recorded.
    """
    prs = _gh_json([
        "gh", "pr", "list",
        "--label", SELF_FIX_LABEL,
        "--state", "closed",
        "--json", "number,state",
        "--limit", "50",
    ])
    issues = _gh_json([
        "gh", "issue", "list",
        "--label", SELF_FIX_LABEL,
        "--state", "closed",
        "--json", "number,state,stateReason",
        "--limit", "50",
    ])

    count = 0
    for pr in prs:
        number = pr.get("number")
        outcome = _STATE_MAP.get(pr.get("state", ""))
        if outcome is None or number is None:
            continue
        pr_budget.record_outcome(
            vault_root=vault_root, pr_number=int(number), outcome=outcome
        )
        count += 1

    for issue in issues:
        number = issue.get("number")
        if number is None or issue.get("state", "") != "CLOSED":
            continue
        # Unlabelled closures pre-date stateReason; treat them as acted on.
        outcome = _ISSUE_REASON_MAP.get(issue.get("stateReason") or "COMPLETED")
        if outcome is None:
            continue
        pr_budget.record_outcome(
            vault_root=vault_root, pr_number=int(number), outcome=outcome
        )
        count += 1

    return count
