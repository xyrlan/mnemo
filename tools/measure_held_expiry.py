"""When do the expiring inbox pages leave, and how many were rewritten since they were staged? (#518)

Usage:
    PYTHONPATH=src python3 tools/measure_held_expiry.py --vault ~/scratch-copy
    PYTHONPATH=src python3 tools/measure_held_expiry.py --vault V --json

**Read only**, and meant for a scratch copy of the vault anyway: it calls
``inbox.staged_pages`` and ``inbox.expires_at`` — the listing's and the
sweep's own function — and reads ``.mnemo/inbox-offers.jsonl``. It never
calls ``expire_held``.

For every page that can expire (:attr:`StagedPage.expiry_why`, restored
pages excluded) it reports three expiry dates:

* ``before`` — the file's mtime + ``heldExpiryDays``: what the sweep read
  before #518, and what every rewrite restarted;
* ``after`` — :func:`inbox.expires_at` as shipped: the ``staged_at`` stamp,
  or the mtime for a page staged before the stamp existed;
* ``ledger`` — the alternative migration #518 offered and did not take: the
  earlier of ``after`` and the first ``offered`` row for the key since it
  was last dropped, expired or promoted.

"Rewritten since first staging" is only observable through that ledger: a
page offered *before* its current mtime (by more than :data:`SLACK_S`) was
on disk before its last write. Pages never offered cannot be told apart, so
the count is a floor.

First run, 2026-09-27 16:21, scratch copy of the maintainer's vault taken
that day: 342 staged, 218 expiring (all ``held``: 206 generic, 12
narrative; 93 of them demotions, 3 multi-source). 20 were ever offered, 9 of
those were rewritten after their first offer. Due now: 0 under every rule.
``before`` and ``after`` are identical (no page carries a stamp yet): 75 on
10-06, 20 on 10-07, 51 on 10-08, 66 on 10-09, 6 on 10-11. The ledger
migration would move 9 pages earlier, 2 of them to 10-03 and none to today.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from mnemo.core import inbox as I

#: A page offered within this many seconds before its mtime is the same write.
SLACK_S = 60
#: Ledger events that end a key's life in the queue; an ``offered`` row before
#: one of them belongs to an earlier staging of the same key.
_ENDS = (I.DROPPED, I.EXPIRED, I.PROMOTED)


def first_offered(rows: List[dict]) -> Dict[str, datetime]:
    """Earliest ``offered`` time per key in its current staging. Rows oldest first."""
    out: Dict[str, datetime] = {}
    for row in rows:
        key = str(row.get("key") or "")
        event = row.get("event")
        if event in _ENDS:
            out.pop(key, None)
        elif event == I.OFFERED and key not in out:
            try:
                out[key] = datetime.fromisoformat(str(row.get("ts")))
            except ValueError:
                continue
    return out


def measure(vault: Path, cfg: Optional[dict] = None, now: Optional[datetime] = None) -> dict:
    cfg = cfg or {}
    now = now or datetime.now()
    days = I.held_expiry_days(cfg)
    restored = I.restored_keys(vault)
    offered = first_offered(I.read_ledger(vault))
    pages = I.staged_pages(vault)
    rows = []
    for page in pages:
        after = I.expires_at(page, cfg, restored=restored)
        if after is None:
            continue
        before = datetime.fromtimestamp(page.mtime) + timedelta(days=days)
        seen = offered.get(page.key)
        ledger = after
        rewritten = False
        if seen is not None:
            ledger = min(after, seen + timedelta(days=days))
            rewritten = seen.timestamp() < page.mtime - SLACK_S
        rows.append({
            "key": page.key,
            "why": page.expiry_why,
            "verdict": page.gate_verdict,
            "stamped": page.staged_at is not None and page.staged_at != page.mtime,
            "rewritten": rewritten,
            "before": before,
            "after": after,
            "ledger": ledger,
        })

    def dist(field: str) -> Dict[str, int]:
        return dict(sorted(Counter(r[field].date().isoformat() for r in rows).items()))

    def due(field: str) -> int:
        return sum(1 for r in rows if r[field] <= now)

    return {
        "now": now.isoformat(timespec="seconds"),
        "days": days,
        "staged": len(pages),
        "expiring": len(rows),
        "by_why": dict(Counter(r["why"] for r in rows)),
        "by_verdict": dict(Counter(r["verdict"] or "-" for r in rows)),
        "offered": sum(1 for r in rows if r["key"] in offered),
        "rewritten": sum(1 for r in rows if r["rewritten"]),
        "rewritten_keys": sorted(r["key"] for r in rows if r["rewritten"]),
        "moved_by_ledger": sum(1 for r in rows if r["ledger"] < r["after"]),
        "due_now": {f: due(f) for f in ("before", "after", "ledger")},
        "by_date": {f: dist(f) for f in ("before", "after", "ledger")},
    }


def render(report: dict) -> str:
    lines = [
        f"now {report['now']}  window {report['days']}d",
        f"staged {report['staged']}  expiring {report['expiring']}  {report['by_why']}",
        f"verdicts {report['by_verdict']}",
        f"offered at least once {report['offered']}; "
        f"rewritten since first offer (floor) {report['rewritten']}",
        f"due now: before {report['due_now']['before']}  after {report['due_now']['after']}  "
        f"ledger-migration {report['due_now']['ledger']} "
        f"(ledger would move {report['moved_by_ledger']} pages earlier)",
        "",
        f"{'expires on':<12} {'before':>7} {'after':>7} {'ledger':>7}",
    ]
    by = report["by_date"]
    for day in sorted(set(by["before"]) | set(by["after"]) | set(by["ledger"])):
        lines.append(f"{day:<12} {by['before'].get(day, 0):>7} "
                     f"{by['after'].get(day, 0):>7} {by['ledger'].get(day, 0):>7}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True, type=Path)
    ap.add_argument("--days", type=int, default=None, help="inbox.heldExpiryDays (default: 14)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    cfg = {} if args.days is None else {"inbox": {"heldExpiryDays": args.days}}
    report = measure(args.vault.expanduser(), cfg)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
