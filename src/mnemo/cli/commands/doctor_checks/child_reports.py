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


def _doctor_check_child_notice_policy(vault: Path) -> bool:
    """Child notices held by Claude Code's inbound policy (#732).

    A bypass parent holds a notice from a child in another permission class
    for the user's approval, every time. Since #741 a child of a bypass parent
    runs bypass itself, so such rows mean an older child, ``dispatch.childMode:
    auto`` or bypass disabled by a setting; the other lever is the user's own
    ``crossSessionInbound`` setting, which mnemo never writes. This names
    both, from the rows that were actually held in the last
    :data:`report_card.LOST_WINDOW_DAYS` days. True when silent.
    """
    import json

    from mnemo.core.sessions import inbound_policy, liveness, report_card

    try:
        text = (vault / ".mnemo" / report_card.LOG_NAME).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return True
    since = time.time() - report_card.LOST_WINDOW_DAYS * 86400
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("held") and (report_card._stamp(row) or 0) >= since:
            rows.append(row)
    setting = inbound_policy.effective_setting(home=liveness.claude_home(), cwd=None)
    lines = inbound_policy.advice(rows, setting=setting)
    for line in lines:
        print(line)
    return not lines
