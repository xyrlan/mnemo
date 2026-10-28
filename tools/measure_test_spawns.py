"""How many processes each test file starts, and which programs they are (#653).

Usage:
    PYTHONPATH=src python3 tools/measure_test_spawns.py tests/unit/test_x.py ... [--json]

Runs pytest in this process with an audit hook on ``subprocess.Popen``, so
every process a test starts through ``subprocess`` is counted against the
file it ran in, by program (``git``, ``python``, ...), alongside the file's
wall time. ``measure_test_durations.py`` ranks the files by time from a CI
log; this says what the slow ones spend it on. Run it serially (no ``-n``):
a worker's processes are not this process's.

Measured at #653 on a Windows 11 dev box, Python 3.13, over the six files
that took 52% of master's Windows CI time (run 39620055686): see the issue
for the table. ``os.system`` and ``os.spawn*`` are not counted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]


def program(executable: Any, args: Any) -> str:
    """The program's bare name, lowercased, without a Windows ``.exe``."""
    first = executable
    if first is None:
        if isinstance(args, (list, tuple)) and args:
            first = args[0]
        elif isinstance(args, (str, bytes)):
            first = args.split()[0] if args.split() else ""
        else:
            first = ""
    if isinstance(first, bytes):
        first = first.decode("utf-8", "replace")
    name = os.path.basename(str(first).replace("\\", "/")).lower()
    for ext in (".exe", ".cmd", ".bat"):
        if name.endswith(ext):
            name = name[: -len(ext)]
    if name.startswith("python"):
        return "python"
    return name


class Counter:
    """A pytest plugin: per file, the processes started and the seconds spent."""

    def __init__(self) -> None:
        self.current: Optional[str] = None
        self.files: Dict[str, Dict[str, Any]] = {}

    def audit(self, event: str, args: Any) -> None:
        if event != "subprocess.Popen" or self.current is None:
            return
        slot = self.files[self.current]["spawns"]
        name = program(args[0], args[1])
        slot[name] = slot.get(name, 0) + 1

    def pytest_runtest_protocol(self, item: Any, nextitem: Any) -> None:
        path = item.nodeid.split("::", 1)[0]
        self.files.setdefault(path, {"file": path, "tests": 0, "seconds": 0.0,
                                     "spawns": {}})
        self.files[path]["tests"] += 1
        self.current = path
        self._t0 = time.perf_counter()

    def pytest_runtest_logfinish(self, nodeid: str, location: Any) -> None:
        if self.current is not None:
            self.files[self.current]["seconds"] += time.perf_counter() - self._t0
        self.current = None


def summarize(files: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for f in files.values():
        total = sum(f["spawns"].values())
        rows.append(dict(f, processes=total,
                         per_test=total / f["tests"] if f["tests"] else 0.0))
    rows.sort(key=lambda r: -r["seconds"])
    return rows


def render(rows: List[Dict[str, Any]]) -> str:
    lines = [f"{'file':44s} {'tests':>5s} {'secs':>6s} {'procs':>6s} {'/test':>6s}  by program"]
    for r in rows:
        by = ", ".join(f"{k} {v}" for k, v in sorted(r["spawns"].items(), key=lambda kv: -kv[1]))
        lines.append(f"{r['file'][-44:]:44s} {r['tests']:5d} {r['seconds']:6.1f} "
                     f"{r['processes']:6d} {r['per_test']:6.1f}  {by}")
    tests = sum(r["tests"] for r in rows)
    secs = sum(r["seconds"] for r in rows)
    procs = sum(r["processes"] for r in rows)
    if procs:
        lines.append(f"{'total':44s} {tests:5d} {secs:6.1f} {procs:6d} "
                     f"{procs / tests:6.1f}  {secs / procs * 1000:.0f} ms of test time per process")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", help="test files or directories")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    import pytest

    counter = Counter()
    sys.addaudithook(counter.audit)  # cannot be removed: one run per process
    code = pytest.main(["-q", "-p", "no:xdist", "-p", "no:cacheprovider"]
                       + list(args.paths), plugins=[counter])
    rows = summarize(counter.files)
    prov = _provenance.provenance(__file__, argv, blind_spots=[
        "processes started outside subprocess (os.system, os.spawn*) are not counted",
        "one machine, serial: CI runs 4 workers on GitHub's Windows runner"])
    if args.json:
        print(json.dumps(_provenance.stamp({"files": rows, "pytest_exit": int(code)}, prov),
                         indent=1))
    else:
        print(render(rows))
        print(_provenance.line(prov))
    return 0 if code in (0, 5) else int(code)


if __name__ == "__main__":
    sys.exit(main())
