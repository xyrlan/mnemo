"""Autopilot Tier 1 — Telemetry anomaly detection.

Scans the MCP access log for telemetry anomalies and opens a draft PR
explaining what's broken so a human can fix the root cause.

Current anomalies detected:
- ``cost_usd_always_zero`` — ``llm.call`` entries have cost_usd = 0 always.
- ``prompt_tokens_null`` — ``llm.call`` entries have null prompt_tokens > threshold.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from mnemo.autopilot.core import pr_budget
from mnemo.autopilot.core.labels import SELF_FIX_LABEL
from mnemo.autopilot.selffix import _gh

_PROMPT_TOKENS_NULL_THRESHOLD = 0.1  # flag if > 10% of llm.call entries have null tokens
_MIN_LLM_CALL_ENTRIES = 5  # don't flag with too few data points


@dataclass
class TelemetryAnomaly:
    """A detected telemetry anomaly."""

    kind: str
    detail: str
    affected_count: int


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def scan_telemetry(*, vault_root: Path) -> List[TelemetryAnomaly]:
    """Scan ``mcp-access-log.jsonl`` for telemetry anomalies.

    Returns a list of :class:`TelemetryAnomaly` objects.
    """
    log_path = vault_root / ".mnemo" / "mcp-access-log.jsonl"
    if not log_path.exists():
        return []

    llm_call_entries: List[dict] = []
    try:
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("event") == "llm.call":
                llm_call_entries.append(entry)
    except OSError:
        return []

    if not llm_call_entries:
        return []

    anomalies: List[TelemetryAnomaly] = []
    anomalies.extend(_check_cost_usd_always_zero(llm_call_entries))
    anomalies.extend(_check_prompt_tokens_null(llm_call_entries))
    return anomalies


def _check_cost_usd_always_zero(entries: List[dict]) -> List[TelemetryAnomaly]:
    """Flag when all llm.call entries have cost_usd == 0 or missing."""
    if len(entries) < _MIN_LLM_CALL_ENTRIES:
        return []
    # Only consider entries that have the cost_usd key
    with_cost = [e for e in entries if "cost_usd" in e]
    if not with_cost:
        return []
    nonzero = [e for e in with_cost if (e.get("cost_usd") or 0) != 0]
    if nonzero:
        return []  # at least one non-zero cost — no anomaly
    return [
        TelemetryAnomaly(
            kind="cost_usd_always_zero",
            detail=(
                f"cost_usd field on llm.call is always 0 across {len(with_cost)} entries "
                f"— pricing table likely not applied"
            ),
            affected_count=len(with_cost),
        )
    ]


def _check_prompt_tokens_null(entries: List[dict]) -> List[TelemetryAnomaly]:
    """Flag when > threshold of llm.call entries have null prompt_tokens."""
    if len(entries) < _MIN_LLM_CALL_ENTRIES:
        return []
    with_field = [e for e in entries if "prompt_tokens" in e]
    if not with_field:
        return []
    null_count = sum(1 for e in with_field if e.get("prompt_tokens") is None)
    if null_count == 0:
        return []
    null_rate = null_count / len(with_field)
    if null_rate <= _PROMPT_TOKENS_NULL_THRESHOLD:
        return []
    return [
        TelemetryAnomaly(
            kind="prompt_tokens_null",
            detail=(
                f"prompt_tokens is null in {null_count}/{len(with_field)} llm.call entries "
                f"({null_rate:.0%}) — possible missing field on early reflex entries"
            ),
            affected_count=null_count,
        )
    ]


# ---------------------------------------------------------------------------
# PR opening
# ---------------------------------------------------------------------------


def open_telemetry_fix_pr(
    anomalies: List[TelemetryAnomaly],
    *,
    vault_root: Path,
    repo_root: Path,
    dry_run: bool = False,
) -> Optional[int]:
    """Open a draft PR explaining the telemetry anomalies.

    Returns the PR number on success, ``None`` otherwise.
    The PR is always opened as a **draft** — a human must fix the root cause.
    """
    if not anomalies:
        return None

    ok, reason = pr_budget.can_open(vault_root=vault_root, category="telemetry_bug")
    if not ok:
        print(f"[autopilot] telemetry PR skipped: {reason}")
        return None

    if dry_run:
        print(f"[autopilot] dry-run: would open telemetry-bug PR for {len(anomalies)} anomaly(ies)")
        for a in anomalies:
            print(f"  • {a.kind}: {a.detail}")
        return None

    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    branch = f"mnemo/self-fix/telemetry-{date_tag}"
    if _gh.create_branch(branch, repo_root=repo_root) is None:
        print("[autopilot] telemetry PR skipped: could not create branch")
        return None

    _gh.push_branch(branch, repo_root=repo_root)
    body_lines = [
        "## Telemetry anomalies detected\n",
        f"Detected {len(anomalies)} telemetry anomaly(ies). "
        "This PR is a **draft** — a human must investigate and fix the root cause.\n",
    ]
    for a in anomalies:
        body_lines.append(f"### `{a.kind}` ({a.affected_count} affected entries)")
        body_lines.append(f"{a.detail}\n")
    body = "\n".join(body_lines)

    pr_number = _gh.open_pr(
        branch=branch,
        title=f"fix(autopilot): telemetry anomalies {date_tag}",
        body=body,
        labels=[SELF_FIX_LABEL],
        draft=True,
        repo_root=repo_root,
    )
    if pr_number is not None:
        pr_budget.record_opened(
            vault_root=vault_root, category="telemetry_bug", pr_number=pr_number
        )
        print(f"[autopilot] opened telemetry-bug PR #{pr_number}")
    return pr_number
