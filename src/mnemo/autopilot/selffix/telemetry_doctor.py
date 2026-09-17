"""Autopilot Tier 1 — Telemetry anomaly detection.

Scans the MCP access log for telemetry anomalies and opens a draft PR
explaining what's broken so a human can fix the root cause.

Current anomalies detected:
- ``cost_usd_always_zero`` — ``llm.call`` entries never carry a nonzero cost_usd.
- ``input_tokens_zero`` — ``llm.call`` entries report 0 input tokens > threshold.

Both read the row ``access_log.record_llm_call`` writes — ``tool`` (not
``event``), a top-level ``cost_usd``, and tokens nested under ``usage``. A check
keyed on a field nothing writes can never fire, and still reads as coverage
(#370).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from mnemo.autopilot.core import network, pr_budget
from mnemo.autopilot.core.labels import SELF_FIX_LABEL
from mnemo.autopilot.selffix import _gh
from mnemo.core.log_utils import iter_rotated_rows

_INPUT_TOKENS_ZERO_THRESHOLD = 0.1  # flag if > 10% of llm.call entries report no input
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

    Reads the rotated ``.1`` file as well as the live one: the anomaly checks
    need ``_MIN_LLM_CALL_ENTRIES`` data points, and a rotation at 1 MiB would
    otherwise reset that sample to whatever landed since (#140).
    """
    log_path = vault_root / ".mnemo" / "mcp-access-log.jsonl"
    llm_call_entries: List[dict] = [
        entry for entry in iter_rotated_rows(log_path)
        if entry.get("tool") == "llm.call"
    ]

    if not llm_call_entries:
        return []

    anomalies: List[TelemetryAnomaly] = []
    anomalies.extend(_check_cost_usd_always_zero(llm_call_entries))
    anomalies.extend(_check_input_tokens_zero(llm_call_entries))
    return anomalies


def _check_cost_usd_always_zero(entries: List[dict]) -> List[TelemetryAnomaly]:
    """Flag when no llm.call entry carries a nonzero cost_usd.

    Rows written before ``record_llm_call`` recorded cost have no ``cost_usd``
    key and are skipped; a row whose key is ``None`` counts — that is the CLI
    reporting no cost, which is the defect this check exists to notice.
    """
    if len(entries) < _MIN_LLM_CALL_ENTRIES:
        return []
    with_cost = [e for e in entries if "cost_usd" in e]
    if len(with_cost) < _MIN_LLM_CALL_ENTRIES:
        return []
    nonzero = [e for e in with_cost if (e.get("cost_usd") or 0) != 0]
    if nonzero:
        return []  # at least one non-zero cost — no anomaly
    return [
        TelemetryAnomaly(
            kind="cost_usd_always_zero",
            detail=(
                f"cost_usd on llm.call is 0 or null across all {len(with_cost)} "
                f"entries — the claude CLI result carried no total_cost_usd, "
                f"or core/llm.py stopped reading it"
            ),
            affected_count=len(with_cost),
        )
    ]


def _input_tokens(entry: dict) -> Optional[int]:
    usage = entry.get("usage")
    if not isinstance(usage, dict) or "input_tokens" not in usage:
        return None
    try:
        return int(usage["input_tokens"] or 0)
    except (TypeError, ValueError):
        return 0


def _check_input_tokens_zero(entries: List[dict]) -> List[TelemetryAnomaly]:
    """Flag when > threshold of llm.call entries report 0 input tokens.

    ``record_llm_call`` writes an unreported count as 0, so 0 is how a missing
    count looks on disk. Every call sends a prompt; a real one is never 0.
    """
    if len(entries) < _MIN_LLM_CALL_ENTRIES:
        return []
    counts = [n for n in (_input_tokens(e) for e in entries) if n is not None]
    if not counts:
        return []
    zero_count = sum(1 for n in counts if n == 0)
    if zero_count == 0:
        return []
    zero_rate = zero_count / len(counts)
    if zero_rate <= _INPUT_TOKENS_ZERO_THRESHOLD:
        return []
    return [
        TelemetryAnomaly(
            kind="input_tokens_zero",
            detail=(
                f"usage.input_tokens is 0 in {zero_count}/{len(counts)} llm.call "
                f"entries ({zero_rate:.0%}) — the claude CLI result's usage block "
                f"was missing or core/llm.py stopped summing it"
            ),
            affected_count=zero_count,
        )
    ]


# ---------------------------------------------------------------------------
# PR opening
# ---------------------------------------------------------------------------


def open_telemetry_fix_pr(
    anomalies: List[TelemetryAnomaly],
    *,
    vault_root: Path,
    repo_root: Optional[Path],
    dry_run: bool = False,
) -> Optional[int]:
    """Open a GitHub **issue** describing the telemetry anomalies.

    An anomaly report changes no files, so there is no diff and therefore no
    pull request to open — a branch cut here would be empty and ``gh pr
    create`` would refuse it. A human must find and fix the root cause, which
    is exactly what an issue is for.

    Returns the issue number on success, ``None`` otherwise.
    """
    if not anomalies:
        return None

    ok, reason = pr_budget.can_open(vault_root=vault_root, category="telemetry_bug")
    if not ok:
        print(f"[autopilot] telemetry PR skipped: {reason}")
        return None

    if dry_run:
        print(
            f"[autopilot] dry-run: would open telemetry-bug issue "
            f"for {len(anomalies)} anomaly(ies)"
        )
        for a in anomalies:
            print(f"  • {a.kind}: {a.detail}")
        return None

    if repo_root is None:
        print("[autopilot] telemetry issue skipped: vault is not a git repo")
        return None

    if not network.enabled():
        print(network.OFF_MESSAGE)
        return None

    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body_lines = [
        "## Telemetry anomalies detected\n",
        f"Detected {len(anomalies)} telemetry anomaly(ies). "
        "Filed by the autopilot — a human must investigate and fix the root cause.\n",
    ]
    for a in anomalies:
        body_lines.append(f"### `{a.kind}` ({a.affected_count} affected entries)")
        body_lines.append(f"{a.detail}\n")
    body = "\n".join(body_lines)

    issue_number = _gh.open_issue(
        title=f"fix(autopilot): telemetry anomalies {date_tag}",
        body=body,
        labels=[SELF_FIX_LABEL],
        repo_root=repo_root,
    )
    if issue_number is not None:
        pr_budget.record_opened(
            vault_root=vault_root, category="telemetry_bug", pr_number=issue_number
        )
        print(f"[autopilot] opened telemetry-bug issue #{issue_number}")
    return issue_number
