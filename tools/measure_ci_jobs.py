"""How long each CI job queues and runs, over a workflow's recent runs (#739).

Usage:
    python3 tools/measure_ci_jobs.py [--branch B] [--event E] [--limit N] [--all] [--json]
    python3 tools/measure_ci_jobs.py --save runs.json ...   # keep what was fetched
    python3 tools/measure_ci_jobs.py --from runs.json       # report it again, offline

A run's wall time is the slowest job's, so the job to make faster is the one
this names, not the one that feels slow. Each run is read through
``gh run list`` and ``gh run view --json jobs``; per job it keeps the queue
(run created to job started) and the run time (job started to completed).
Only successful jobs count unless ``--all``: a failed job stops early and a
cancelled one at the timeout, and either would skew the median.

``--save`` writes the fetched runs as JSON and ``--from`` reads them back,
so a before/after comparison can be reported again from the same data.

Measured at #739, Windows job: median 803 s (721–846) over 7 serial PR runs
before, 306 s (287–377) over the PR's 3 attempts under ``-n auto``; the run's
wall time went from a median of 806 s to 377 s (the one attempt that had
every job; GitHub cancelled an unacquired macOS job in the other two).
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

Gh = Callable[[Sequence[str]], str]


def _gh(args: Sequence[str]) -> str:
    return subprocess.run(["gh"] + list(args), capture_output=True, text=True,
                          check=True, encoding="utf-8").stdout


def _ts(value: str) -> datetime:
    # ``fromisoformat`` only reads a trailing ``Z`` from 3.11 on.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch(workflow: str, *, limit: int, branch: Optional[str] = None,
          event: Optional[str] = None, gh: Gh = _gh) -> List[Dict[str, Any]]:
    """Completed runs of ``workflow``, newest first, each with its ``jobs``."""
    args = ["run", "list", "--workflow", workflow, "--status", "completed",
            "--limit", str(limit),
            "--json", "databaseId,attempt,createdAt,startedAt,event,headBranch,conclusion"]
    if branch:
        args += ["--branch", branch]
    if event:
        args += ["--event", event]
    runs = json.loads(gh(args))
    for run in runs:
        view = json.loads(gh(["run", "view", str(run["databaseId"]), "--json", "jobs"]))
        run["jobs"] = view["jobs"]
    return runs


def job_times(run: Dict[str, Any], *, include_failed: bool = False) -> List[Dict[str, Any]]:
    """``[{"job", "queue", "run", "conclusion"}]`` in seconds, for the jobs that finished.

    A re-run keeps the run's ``createdAt`` and shows only its latest attempt's
    jobs, so the queue counts from ``startedAt``, the attempt's own start.
    """
    created = _ts(run.get("startedAt") or run["createdAt"])
    out = []
    for job in run.get("jobs", []):
        if not job.get("startedAt") or not job.get("completedAt"):
            continue
        if not include_failed and job.get("conclusion") != "success":
            continue
        started, completed = _ts(job["startedAt"]), _ts(job["completedAt"])
        out.append({"job": job["name"], "conclusion": job.get("conclusion"),
                    "queue": (started - created).total_seconds(),
                    "run": (completed - started).total_seconds()})
    return out


def summarize(runs: Sequence[Dict[str, Any]], *, include_failed: bool = False) -> Dict[str, Any]:
    """Per job: samples, median/min/max run time and median queue; per run: wall time.

    A run's wall time is creation to its last job's completion, and is kept
    only for a run whose every job counted, since a missing job hides the
    one that may have been slowest.
    """
    per_job: Dict[str, Dict[str, List[float]]] = {}
    walls = []
    for run in runs:
        times = job_times(run, include_failed=include_failed)
        for t in times:
            slot = per_job.setdefault(t["job"], {"run": [], "queue": []})
            slot["run"].append(t["run"])
            slot["queue"].append(t["queue"])
        if times and len(times) == len(run.get("jobs", [])):
            walls.append(max(t["queue"] + t["run"] for t in times))
    jobs = [{"job": name, "samples": len(s["run"]),
             "run_median": statistics.median(s["run"]),
             "run_min": min(s["run"]), "run_max": max(s["run"]),
             "queue_median": statistics.median(s["queue"])}
            for name, s in per_job.items()]
    jobs.sort(key=lambda j: -j["run_median"])
    return {"runs": len(runs), "jobs": jobs,
            "wall": ({"samples": len(walls), "median": statistics.median(walls),
                      "min": min(walls), "max": max(walls)} if walls else None)}


def render(summary: Dict[str, Any]) -> str:
    lines = [f"{'job':40s} {'n':>3s} {'median':>7s} {'min':>7s} {'max':>7s} {'queue':>7s}"]
    for j in summary["jobs"]:
        lines.append(f"{j['job'][:40]:40s} {j['samples']:3d} {j['run_median']:7.0f} "
                     f"{j['run_min']:7.0f} {j['run_max']:7.0f} {j['queue_median']:7.0f}")
    wall = summary["wall"]
    if wall:
        lines.append(f"{'run wall time (all jobs counted)':40s} {wall['samples']:3d} "
                     f"{wall['median']:7.0f} {wall['min']:7.0f} {wall['max']:7.0f}")
    lines.append(f"(seconds; {summary['runs']} runs read)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workflow", default="CI")
    ap.add_argument("--branch", default=None)
    ap.add_argument("--event", default=None, help="pull_request, push, ...")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--all", action="store_true", help="count failed and cancelled jobs too")
    ap.add_argument("--save", default=None, help="write the fetched runs to this JSON file")
    ap.add_argument("--from", dest="source", default=None, help="read runs from a --save file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.source:
        with open(args.source, encoding="utf-8") as fh:
            runs = json.load(fh)
    else:
        runs = fetch(args.workflow, limit=args.limit, branch=args.branch, event=args.event)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump(runs, fh, indent=1)
    summary = summarize(runs, include_failed=args.all)
    prov = _provenance.provenance(__file__, argv, blind_spots=[
        "runner load: one sample per job per run, on whatever machine GitHub assigned"])
    if args.json:
        print(json.dumps(_provenance.stamp(summary, prov), indent=1))
    else:
        print(render(summary))
        print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
