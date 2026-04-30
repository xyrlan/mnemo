"""Autopilot Tier 0 — miss → rule_candidate accumulator.

Reads recall-report.json, writes a ``rule_candidate`` proposal for each
miss that doesn't already have a pending proposal.

Public API:
    collect_recall_misses(vault_root) -> int   # number of NEW proposals written
"""
from __future__ import annotations

from pathlib import Path

from mnemo.autopilot.insights._log_readers import read_recall_report
from mnemo.autopilot.core.proposals import write_proposal, list_proposals


def collect_recall_misses(*, vault_root: Path) -> int:
    """Write a ``rule_candidate`` proposal for each recall miss.

    Idempotent: if a proposal with the same ``expected_slug`` *and*
    ``project`` is already pending, it is skipped.

    Returns the number of newly-written proposals.
    """
    report = read_recall_report(vault_root)
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
