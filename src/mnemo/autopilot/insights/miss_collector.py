"""Autopilot Tier 0 — miss → rule_candidate accumulator.

Reads recall-report.json, writes a ``rule_candidate`` proposal for each
miss that doesn't already have a pending proposal.

Public API:
    collect_recall_misses(vault_root) -> int   # number of NEW proposals written
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from mnemo.autopilot.insights._log_readers import read_recall_report
from mnemo.autopilot.core.proposals import write_proposal, list_proposals

#: Recall reports older than this are considered stale; the collector will
#: refresh them before running so proposals reflect current ranking.
RECALL_REPORT_STALE_DAYS = 7


def _parse_iso_z(ts: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _is_report_stale(report: dict, *, now: datetime | None = None) -> bool:
    """True iff the report's ``generated_at`` is missing or older than the threshold."""
    ts = (report or {}).get("generated_at") or ""
    parsed = _parse_iso_z(ts)
    if parsed is None:
        return True
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=RECALL_REPORT_STALE_DAYS)
    return parsed < cutoff


def _refresh_recall_report(vault_root: Path) -> dict | None:
    """Re-run the recall pipeline against ``vault_root`` and persist a fresh report.

    Returns the freshly-loaded report, or ``None`` if recall could not run
    (e.g., access log missing or no cases yet).
    """
    import json

    from mnemo.core.mcp.recall import (
        aggregate, bootstrap_cases, count_log_entries, run_case,
    )

    log_path = vault_root / ".mnemo" / "mcp-access-log.jsonl"
    if not log_path.is_file():
        return None
    try:
        cases, orphan_dropped = bootstrap_cases(
            log_path, pair_window_s=60, vault_root=vault_root, return_orphan_count=True,
        )
    except Exception:
        return None
    if not cases:
        return None
    try:
        results = [run_case(vault_root, c) for c in cases]
        log_entries = count_log_entries(log_path)
        report = aggregate(results, log_entries=log_entries, orphan_dropped=orphan_dropped)
    except Exception:
        return None
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "report": report,
        "results": results,
    }
    mnemo_dir = vault_root / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    (mnemo_dir / "recall-report.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def collect_recall_misses(*, vault_root: Path) -> int:
    """Write a ``rule_candidate`` proposal for each recall miss.

    Idempotent: if a proposal with the same ``expected_slug`` *and*
    ``project`` is already pending, it is skipped.

    Returns the number of newly-written proposals.
    """
    report = read_recall_report(vault_root)
    if report is None or _is_report_stale(report):
        refreshed = _refresh_recall_report(vault_root)
        if refreshed is not None:
            report = refreshed
    if report is None:
        return 0

    results = report.get("results") or []
    generated_at = report.get("generated_at", "")

    # Build set of already-pending (slug, project) pairs to enforce idempotency.
    pending = list_proposals(vault_root=vault_root, kind="rule_candidate", status="pending")
    pending_keys: set = set()
    for p in pending:
        if p.source == "tier0.miss_collector":
            slug = p.payload.get("expected_slug", "")
            project = p.project or ""
            pending_keys.add((slug, project))

    written = 0
    for result in results:
        if result.get("hit"):
            continue
        slug = result.get("expect_slug", "")
        project = result.get("project") or ""
        if not slug:
            continue
        key = (slug, project)
        if key in pending_keys:
            continue

        rank = result.get("rank")
        result_count = result.get("result_count", 0)
        if rank is None:
            reason = f"miss in recall — not in top {result_count}"
        else:
            reason = f"miss in recall — ranked {rank}/{result_count}"

        write_proposal(
            vault_root=vault_root,
            kind="rule_candidate",
            source="tier0.miss_collector",
            project=project or None,
            confidence=0.0,
            payload={
                "expected_slug": slug,
                "topic": result.get("topic", ""),
                "reason": reason,
                "recall_report_at": generated_at,
            },
        )
        pending_keys.add(key)
        written += 1

    return written
