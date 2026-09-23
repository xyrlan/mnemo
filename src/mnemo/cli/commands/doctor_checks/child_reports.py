"""Doctor check — a dispatched child's report that never came (#460).

``SessionEnd`` starts a detached ``mnemo child-report`` for a dispatched child
and writes a ``spawned`` row to ``.mnemo/child-reports.jsonl``. The reporter
writes a row for everything it does, including crashing and being signalled.
A ``spawned`` row with nothing after it is a reporter that died where it
could not say so, and a parent that was never told its child finished. On
2026-09-23 the maintainer found one only because the child was missing from
their queue; this check is where it shows instead.
"""
from __future__ import annotations

import time
from pathlib import Path


def _doctor_check_child_reports(vault: Path) -> bool:
    """Doctor-registry adapter — True when silent, False on a warning."""
    from mnemo.core.sessions import report_card

    rows = report_card.lost(vault)
    if not rows:
        return True
    print(
        f"  ⚠ child reports: {len(rows)} reporter(s) in the last "
        f"{report_card.LOST_WINDOW_DAYS} days started and wrote nothing after; "
        f"their parent was never told the child finished"
    )
    for row in rows:
        at = report_card._stamp(row)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else "?"
        parent = str(row.get("parent") or "")[:8] or "?"
        print(f"    {row.get('short_id')} -> {parent} at {when} (reporter pid {row.get('pid')})")
    print(
        "    → `mnemo sessions` has each child's state and PR. A reporter that "
        "crashes or gets SIGTERM/SIGHUP/SIGINT leaves a row saying so, so one "
        "listed here was SIGKILLed or died before it started"
    )
    return False
