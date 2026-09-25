"""Which dispatched children stopped without their parent being told (#502).

Usage:
    PYTHONPATH=src python3 tools/measure_child_notices.py [SINCE] [--json] [--list]

``SINCE`` is an ISO prefix compared against each session's ``updatedAt``
(default ``2026-09-24T12``). Read-only: no LLM calls, no network, no writes.

For every child in ``mnemo sessions --all --stale --json`` that has a parent
link (``dispatch-parents.jsonl``), is ``stopped`` and stopped after SINCE, it
joins:

- ``<vault>/.mnemo/child-reports.jsonl`` — any row means the parent was told.
  A row carrying ``via: backstop`` was written by
  :mod:`mnemo.core.sessions.child_notices` rather than by the hook;
- ``$TMPDIR/mnemo/session-<sid>.json`` — still present means SessionEnd never
  reached ``session.clear``. Only meaningful within 24 h of the stop:
  ``session.cleanup_stale`` deletes older files;
- the child transcript — the last ``claude stop`` tool call (did it stop
  itself?) and the last assistant turn;
- ``~/.claude/daemon.log`` — the ``bg settled <short>`` line.

It prints one line per group with the stop→settled delay, and for reported
children the delay from the last assistant turn to the first report row —
which is how long the hook takes to say anything, and what
``child_notices.GRACE_SECONDS`` has to exceed.

This is the script in #502's body, made testable; its groups are the same.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

REPORTED_HOOK = "reported by SessionEnd"
REPORTED_BACKSTOP = "reported by backstop"
SILENT_NEVER_RAN = "silent, SessionEnd never ran"
SILENT_RAN = "silent, SessionEnd ran"
GROUPS = (REPORTED_HOOK, REPORTED_BACKSTOP, SILENT_NEVER_RAN, SILENT_RAN)

_SETTLED = re.compile(r"\[(\S+?)Z?\] \[bg\] bg settled (\w+) ")


def _epoch(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Report rows: 2026-09-24T21:14:02-0300.
    m = re.match(r"^(.*[T ]\d\d:\d\d:\d\d(?:\.\d+)?)([+-]\d\d)(\d\d)$", text)
    if m:
        text = f"{m.group(1)}{m.group(2)}:{m.group(3)}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # daemon.log stamps are UTC with the Z stripped by the pattern above.
        from datetime import timezone

        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ---------------------------------------------------------------------------
# readers — each takes text or an iterable of lines, so tests pass fixtures
# ---------------------------------------------------------------------------


def parse_parents(lines: Iterable[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("short_id") and row.get("parent_session"):
            out[row["short_id"]] = row["parent_session"]
    return out


def parse_reports(lines: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    """``short_id -> rows``, every row, in file order."""
    out: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("short_id"):
            out[row["short_id"]].append(row)
    return dict(out)


def parse_settled(lines: Iterable[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for line in lines:
        m = _SETTLED.match(line)
        if m:
            at = _epoch(m.group(1))
            if at is not None:
                out[m.group(2)] = at
    return out


def read_transcript(lines: Iterable[str]) -> Dict[str, Optional[float]]:
    """``{"stop": last claude-stop tool call, "turn": last assistant turn}``."""
    stop = turn = None
    for line in lines:
        if '"assistant"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("type") != "assistant":
            continue
        at = _epoch(record.get("timestamp"))
        if at is None:
            continue
        turn = at
        for block in (record.get("message") or {}).get("content") or []:
            if (isinstance(block, dict) and block.get("type") == "tool_use"
                    and "claude stop" in json.dumps(block.get("input"))):
                stop = at
    return {"stop": stop, "turn": turn}


# ---------------------------------------------------------------------------
# the join
# ---------------------------------------------------------------------------


def classify(rows: List[Dict[str, Any]], cache_left: bool) -> str:
    if rows:
        return REPORTED_BACKSTOP if any(r.get("via") == "backstop" for r in rows) else REPORTED_HOOK
    return SILENT_NEVER_RAN if cache_left else SILENT_RAN


def measure(
    roster: List[Dict[str, Any]],
    *,
    since: str,
    parents: Dict[str, str],
    reports: Dict[str, List[Dict[str, Any]]],
    settled: Dict[str, float],
    cache_left: Any,
    transcript: Any,
) -> List[Dict[str, Any]]:
    """One row per dispatched child that stopped after *since*.

    *cache_left(session_id) -> bool* and *transcript(session_id) -> dict*
    (as :func:`read_transcript` returns) are the two filesystem reads.
    """
    out = []
    for s in roster:
        sid = s.get("short_id")
        if sid not in parents or s.get("state") != "stopped":
            continue
        if (s.get("updated_at") or "") < since:
            continue
        full = s.get("session_id") or ""
        rows = reports.get(sid) or []
        kind = classify(rows, bool(cache_left(full)))
        t = transcript(full) or {}
        stop, turn = t.get("stop"), t.get("turn")
        done = settled.get(sid)
        first_row = None
        if turn is not None:
            after = [a for a in (_epoch(r.get("ts")) for r in rows) if a is not None and a >= turn - 2]
            first_row = min(after) if after else None
        out.append({
            "short_id": sid,
            "group": kind,
            "self_stopped": stop is not None,
            "stop_to_settled": round(done - stop, 1) if stop is not None and done is not None else None,
            "turn_to_report": round(first_row - turn, 1) if first_row is not None else None,
        })
    return out


def _median(values: List[float]) -> Optional[float]:
    values = sorted(values)
    return values[len(values) // 2] if values else None


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    groups: Dict[str, Any] = {}
    for kind in GROUPS:
        mine = [r for r in rows if r["group"] == kind]
        delays = sorted(r["stop_to_settled"] for r in mine if r["stop_to_settled"] is not None)
        reports = sorted(r["turn_to_report"] for r in mine if r["turn_to_report"] is not None)
        groups[kind] = {
            "children": len(mine),
            "self_stopped": sum(1 for r in mine if r["self_stopped"]),
            "stop_to_settled_median": _median(delays),
            "stop_to_settled_range": [delays[0], delays[-1]] if delays else None,
            "turn_to_report_median": _median(reports),
            "turn_to_report_max": reports[-1] if reports else None,
        }
    return {"total": len(rows), "groups": groups}


def render(summary: Dict[str, Any], since: str) -> str:
    lines = [f"dispatched children stopped since {since}: {summary['total']}"]
    for kind in GROUPS:
        g = summary["groups"][kind]
        line = f"  {kind:30} {g['children']:3}  self-stopped {g['self_stopped']:3}"
        if g["stop_to_settled_range"]:
            line += (f"  stop->settled median {g['stop_to_settled_median']}s "
                     f"range {g['stop_to_settled_range']}")
        if g["turn_to_report_max"] is not None:
            line += (f"  turn->report median {g['turn_to_report_median']}s "
                     f"max {g['turn_to_report_max']}s")
        lines.append(line)
    silent = summary["groups"][SILENT_NEVER_RAN]["children"] + summary["groups"][SILENT_RAN]["children"]
    lines.append(f"parents never told: {silent} of {summary['total']}")
    return "\n".join(lines)


def simulate(
    roster: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    *,
    links: Dict[str, str],
    reports: Dict[str, List[Dict[str, Any]]],
    find: Any,
    turn_of: Any,
) -> Dict[str, Dict[str, int]]:
    """What ``child_notices.scan`` decides about each measured child, run on
    the same files, one grace period after its stop: ``{group: {"due": n,
    "told": n}}``. ``due`` is a notice the backstop would send.

    *reports* is ``child_notices.report_rows`` output — the scan's own reader,
    so this runs the shipped code rather than a copy of its rule.
    """
    from types import SimpleNamespace

    from mnemo.core.sessions import child_notices

    by_id = {s.get("short_id"): s for s in roster}
    out: Dict[str, Dict[str, int]] = {k: {"due": 0, "told": 0} for k in GROUPS}
    for row in rows:
        s = by_id.get(row["short_id"]) or {}
        stopped_at = child_notices._epoch(s.get("updated_at"))
        if stopped_at is None:
            continue
        session = SimpleNamespace(
            short_id=s.get("short_id"), session_id=s.get("session_id"), state=s.get("state"),
            updated_at=s.get("updated_at"), cwd=s.get("cwd"), live=s.get("live"),
            link_scan_path=s.get("link_scan_path"), parent_session=None,
        )
        found = child_notices.scan(
            [session], links=links, rows=reports,
            now=stopped_at + child_notices.GRACE_SECONDS + 1, armed_at=0.0,
            find=find, turn_of=turn_of,
        )
        out[row["group"]]["due" if found.due else "told"] += 1
    return out


# ---------------------------------------------------------------------------
# the real machine
# ---------------------------------------------------------------------------


def _lines(path: str) -> List[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("since", nargs="?", default="2026-09-24T12")
    ap.add_argument("--vault", default=None, help="vault root (default: mnemo's configured vault)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true", help="one line per child")
    ap.add_argument("--simulate", action="store_true",
                    help="also run child_notices.scan on each child, one grace period after its stop")
    args = ap.parse_args(argv)

    if args.vault:
        vault = os.path.expanduser(args.vault)
    else:
        from mnemo.core import config, paths

        vault = str(paths.vault_root(config.load_config()))
    roster = json.loads(subprocess.run(
        ["mnemo", "sessions", "--all", "--stale", "--json"],
        capture_output=True, text=True, check=True).stdout)
    cache = os.path.join(tempfile.gettempdir(), "mnemo")
    projects = os.path.expanduser("~/.claude/projects")

    def transcript(full: str) -> Dict[str, Optional[float]]:
        found = glob.glob(os.path.join(projects, "*", f"{full}.jsonl"))
        return read_transcript(_lines(found[0])) if found else {}

    parents = parse_parents(_lines(os.path.join(vault, ".mnemo", "dispatch-parents.jsonl")))
    rows = measure(
        roster, since=args.since,
        parents=parents,
        reports=parse_reports(_lines(os.path.join(vault, ".mnemo", "child-reports.jsonl"))),
        settled=parse_settled(_lines(os.path.expanduser("~/.claude/daemon.log"))),
        cache_left=lambda full: os.path.exists(os.path.join(cache, f"session-{full}.json")),
        transcript=transcript,
    )
    summary = summarize(rows)
    if args.simulate:
        from pathlib import Path

        from mnemo.core.sessions import child_notices

        summary["simulated"] = simulate(
            roster, rows, links=parents, reports=child_notices.report_rows(Path(vault)),
            find=child_notices.transcript_for, turn_of=child_notices.last_turn,
        )
    if args.json:
        print(json.dumps({"since": args.since, **summary, "children": rows}, indent=2))
        return 0
    print(render(summary, args.since))
    for kind, got in (summary.get("simulated") or {}).items():
        if got["due"] or got["told"]:
            print(f"  backstop on {kind:30} would tell {got['due']:3}, leaves {got['told']:3} as told")
    if args.list:
        for r in sorted(rows, key=lambda r: (r["group"], r["short_id"])):
            print(f"  {r['short_id']}  {r['group']:30} stop->settled {r['stop_to_settled']}  "
                  f"turn->report {r['turn_to_report']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
