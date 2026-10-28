"""Where a CI job's pytest step spends its time, per test file, from its log (#653).

Usage:
    gh run view RUN --job JOB --log > job.log
    python3 tools/measure_test_durations.py job.log [--top N] [--source ROOT] [--json]

Reads the ``pytest -v -n auto`` lines GitHub timestamps, one per test report:
``2026-10-21T14:49:15.89Z [gw0] [  0%] PASSED tests/x.py::test_y``. A worker
runs one test at a time, so a test's time is the gap from its worker's
previous report to its own; the first test of each worker counts from the
``scheduling tests via`` line. The gaps of one worker sum to its busy time,
and the files are ranked by the sum of their tests' gaps.

``--source ROOT`` reads each ranked file under ROOT and marks the ones that
start processes (``subprocess``, ``Popen``, ``sys.executable``, ``os.system``),
and the summary splits the worker time by that mark. The mark is the file's
text, not what ran: a file that imports ``subprocess`` for one test is marked
whole.

Measured at #653 on master's run 39620055686 (2026-10-21, 4 workers): the
step took 356 s, 29 s of it before the first test; the 4 workers were busy
1300 s between them.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

_TS = r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)"
_REPORT = re.compile(_TS + r"\s+\[(gw\d+)\]\s+\[\s*\d+%\]\s+"
                     # A parametrized id can hold spaces: the node id is the rest.
                     r"(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\s+(.+?)\s*$")
_SCHEDULING = re.compile(_TS + r"\s+scheduling tests via")
_STARTS = re.compile(_TS + r"\s+=+ test session starts")
_SPAWNS = re.compile(r"\bsubprocess\b|\bPopen\b|sys\.executable|os\.system")


def _ts(value: str) -> datetime:
    # ``fromisoformat`` reads neither a trailing ``Z`` before 3.11 nor more
    # than six fractional digits, and GitHub writes seven.
    value = value.rstrip("Z")
    if "." in value:
        head, frac = value.split(".")
        value = head + "." + frac[:6].ljust(6, "0")
    return datetime.fromisoformat(value)


def parse(lines: Iterable[str]) -> Dict[str, Any]:
    """``{"start", "session", "reports"}``: report rows ``(ts, worker, outcome, nodeid)``."""
    start = session = None
    reports = []
    for line in lines:
        m = _REPORT.search(line)
        if m:
            reports.append((_ts(m.group(1)), m.group(2), m.group(3), m.group(4)))
            continue
        m = _SCHEDULING.search(line)
        if m and start is None:
            start = _ts(m.group(1))
            continue
        m = _STARTS.search(line)
        if m and session is None:
            session = _ts(m.group(1))
    return {"start": start, "session": session, "reports": reports}


def test_times(parsed: Dict[str, Any]) -> Dict[str, float]:
    """Seconds per node id: the gap since its worker's previous report.

    A test that reports more than once (an error in teardown after a pass)
    gets the sum of its gaps, so nothing a worker did is dropped.
    """
    reports = sorted(parsed["reports"], key=lambda r: r[0])
    last: Dict[str, datetime] = {}
    out: Dict[str, float] = {}
    origin = parsed["start"] or (reports[0][0] if reports else None)
    for ts, worker, _outcome, nodeid in reports:
        prev = last.get(worker, origin)
        out[nodeid] = out.get(nodeid, 0.0) + max(0.0, (ts - prev).total_seconds())
        last[worker] = ts
    return out


def _file(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def summarize(parsed: Dict[str, Any], *, source: Optional[Path] = None,
              top: int = 25) -> Dict[str, Any]:
    times = test_times(parsed)
    files: Dict[str, Dict[str, Any]] = {}
    for nodeid, secs in times.items():
        f = files.setdefault(_file(nodeid), {"file": _file(nodeid), "seconds": 0.0,
                                             "tests": 0, "slowest": 0.0})
        f["seconds"] += secs
        f["tests"] += 1
        f["slowest"] = max(f["slowest"], secs)
    ranked = sorted(files.values(), key=lambda f: -f["seconds"])
    busy = sum(times.values())
    for f in ranked:
        f["share"] = f["seconds"] / busy if busy else 0.0
        f["per_test"] = f["seconds"] / f["tests"]
    spawn = None
    if source is not None:
        spawn = {"spawning": {"files": 0, "tests": 0, "seconds": 0.0},
                 "other": {"files": 0, "tests": 0, "seconds": 0.0}, "unread": 0}
        for f in ranked:
            try:
                text = (source / f["file"]).read_text(encoding="utf-8")
            except OSError:
                f["spawns"] = None
                spawn["unread"] += 1
                continue
            f["spawns"] = bool(_SPAWNS.search(text))
            slot = spawn["spawning" if f["spawns"] else "other"]
            slot["files"] += 1
            slot["tests"] += f["tests"]
            slot["seconds"] += f["seconds"]
    reports = parsed["reports"]
    first = min((r[0] for r in reports), default=None)
    end = max((r[0] for r in reports), default=None)
    head = parsed["session"] or parsed["start"]
    return {
        "tests": len(times), "files": len(files),
        "workers": len({r[1] for r in reports}),
        "busy_seconds": busy,
        "tests_wall_seconds": ((end - (parsed["start"] or first)).total_seconds()
                               if end else 0.0),
        "session_to_scheduling_seconds": ((parsed["start"] - head).total_seconds()
                                          if parsed["start"] and head else None),
        "top": ranked[:top],
        "top_share": sum(f["share"] for f in ranked[:top]),
        "spawn": spawn,
    }


def render(s: Dict[str, Any]) -> str:
    lines = [f"{s['tests']} tests in {s['files']} files on {s['workers']} workers: "
             f"{s['tests_wall_seconds']:.0f} s from scheduling to the last report, "
             f"{s['busy_seconds']:.0f} worker-seconds busy",
             "",
             f"{'file':62s} {'secs':>6s} {'share':>6s} {'tests':>5s} {'s/test':>6s} "
             f"{'max':>5s}"]
    for f in s["top"]:
        mark = {True: " P", False: "", None: " ?"}[f.get("spawns", False)]
        lines.append(f"{f['file'][-62:]:62s} {f['seconds']:6.1f} {f['share']:6.1%} "
                     f"{f['tests']:5d} {f['per_test']:6.2f} {f['slowest']:5.1f}{mark}")
    lines.append(f"(top {len(s['top'])} files: {s['top_share']:.1%} of the busy time)")
    sp = s["spawn"]
    if sp:
        lines.append("")
        lines.append("P = the file's text starts processes; all files, by that mark:")
        for key in ("spawning", "other"):
            v = sp[key]
            share = v["seconds"] / s["busy_seconds"] if s["busy_seconds"] else 0.0
            per = v["seconds"] / v["tests"] if v["tests"] else 0.0
            lines.append(f"  {key:9s} {v['files']:4d} files {v['tests']:5d} tests "
                         f"{v['seconds']:7.0f} s ({share:.1%}), {per:.3f} s/test")
        if sp["unread"]:
            lines.append(f"  unread    {sp['unread']:4d} files")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log", help="a job log, e.g. from `gh run view RUN --job JOB --log`")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--source", default=None,
                    help="checkout root, to mark files that start processes")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with open(args.log, encoding="utf-8", errors="replace") as fh:
        parsed = parse(fh)
    summary = summarize(parsed, source=Path(args.source) if args.source else None,
                        top=args.top)
    prov = _provenance.provenance(__file__, argv, blind_spots=[
        "a test's time is the gap between its worker's reports, so it includes "
        "fixture setup and teardown and any wait for the controller",
        "one log is one runner: its load and CPU count are GitHub's choice"])
    if args.json:
        print(json.dumps(_provenance.stamp(summary, prov), indent=1, default=str))
    else:
        print(render(summary))
        print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
