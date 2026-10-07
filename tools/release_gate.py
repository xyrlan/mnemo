"""Refuse to release a commit whose CI is not green (#594).

``release.yml`` checked that the tag matches the version and that no changelog
fragment is pending, never the CI result of the commit it publishes, and
``gh pr merge`` does not gate on checks either. A tag on a red commit shipped
to PyPI, npm and the plugin anyway. This is the first job of the release:

- **pass** — every job of the CI workflow (``.github/workflows/ci.yml``) on the
  tagged SHA concluded ``success``, or failed and a rerun of the same job on
  the same commit succeeded (GitHub's own verdict that the failure was not
  the commit's).
- **fail** — a job is red, skipped or cancelled; or GitHub never started it
  (the billing annotation, #575), reported apart because no code was tested;
  or there is no CI run for the SHA at all.
- **pending** — CI is still running, or GitHub could not be reached. The gate
  polls every ``--interval`` seconds for at most ``--timeout-minutes``, then
  fails with a message saying to re-run the release once CI finishes. Tagging
  right after the release PR merges is the normal path, so CI on master is
  usually still running when the tag lands; failing at once would fail most
  releases. On master, 40 push runs of CI took 1.0-6.9 min (``gh run list
  --workflow ci.yml --branch master --event push --limit 40``, 2026-10-07),
  so the 30 min default is over four times the slowest.

Only the CI workflow's check runs are read: the release workflow's own jobs sit
on the same SHA and are still running while this one does. The reading of a
failed check is ``mnemo.core.sessions.check_runs``, the one ``pr-follow`` uses.

    PYTHONPATH=src python3 tools/release_gate.py --repo owner/name --sha <sha>

``--repo`` and ``--sha`` default to ``GITHUB_REPOSITORY`` and ``GITHUB_SHA``.
Exit 0 to release, 1 to refuse.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mnemo.core.sessions import check_runs  # noqa: E402

Runner = check_runs.Runner

CI_WORKFLOW = ".github/workflows/ci.yml"

PASS = "pass"
FAIL = "fail"
PENDING = "pending"


def _gh(argv: Sequence[str], stdin: Optional[str]) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(list(argv), input=stdin, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        return 127, "", str(e)
    return p.returncode, p.stdout, p.stderr


def ci_runs(repo: str, sha: str, *, run: Runner) -> Optional[List[Dict[str, Any]]]:
    """The CI workflow's runs on *sha*, or ``None`` when ``gh`` could not be asked."""
    code, out, _ = run(["gh", "api", f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100",
                        "--jq", "[.workflow_runs[] | {id, path, status, conclusion, "
                                "check_suite_id}]"], None)
    if code != 0:
        return None
    try:
        runs = json.loads(out or "[]")
    except ValueError:
        return None
    if not isinstance(runs, list):
        return None
    return [r for r in runs if isinstance(r, dict) and r.get("path") == CI_WORKFLOW]


def _names(runs: Sequence[Dict[str, Any]]) -> str:
    return ", ".join(sorted({str(r.get("name")) for r in runs}))


def judge(repo: str, sha: str, *, run: Runner) -> Tuple[str, str]:
    """``(PASS | FAIL | PENDING, why)`` for the CI of *sha* as it stands now."""
    short = sha[:12]
    workflows = ci_runs(repo, sha, run=run)
    if workflows is None:
        return PENDING, f"could not ask GitHub for the CI runs of {short}"
    if not workflows:
        return FAIL, (f"no CI run for {short}: {CI_WORKFLOW} never ran on this commit, "
                      "so nothing about it was tested. Tag a commit CI ran on.")
    if any(w.get("status") != "completed" for w in workflows):
        return PENDING, f"CI on {short} is still running"
    checks = check_runs.commit_runs(repo, sha, run=run)
    if checks is None:
        return PENDING, f"could not ask GitHub for the check runs of {short}"
    suites = {w.get("check_suite_id") for w in workflows}
    jobs = [c for c in checks if c.get("suite") in suites]
    if not jobs:
        concl = ", ".join(sorted({str(w.get("conclusion")) for w in workflows}))
        return FAIL, f"CI on {short} concluded {concl} with no job run"
    if any(c.get("status") != "completed" for c in jobs):
        return PENDING, f"CI on {short} is still running"

    not_green = [c for c in jobs if c.get("conclusion") != "success"
                 and not check_runs.rerun_passed(c, jobs)]
    if not not_green:
        return PASS, f"CI on {short} is green: {len({c.get('name') for c in jobs})} jobs"
    never, red = [], []
    for c in not_green:
        notes = check_runs.annotations(repo, c.get("id"), run=run) \
            if check_runs.failed(c) else []
        (never if check_runs.never_started(notes) else red).append(c)
    if red:
        return FAIL, (f"CI on {short} is not green: {_names(red)}. "
                      "Fix it, or re-run CI if the failure was not the commit's, "
                      "then re-run this release.")
    return FAIL, (f"CI never ran on {short}: GitHub did not start {_names(never)} "
                  "(the job annotation says the account's payments failed or its "
                  "spending limit was reached). No code was tested. Fix Actions "
                  "billing, re-run CI, then re-run this release.")


def wait(repo: str, sha: str, *, run: Runner, timeout: float, interval: float,
         clock: Callable[[], float] = time.monotonic,
         sleep: Callable[[float], None] = time.sleep) -> Tuple[bool, str]:
    """Judge *sha* until CI settles or *timeout* seconds pass."""
    deadline = clock() + timeout
    while True:
        state, why = judge(repo, sha, run=run)
        if state != PENDING:
            return state == PASS, why
        left = deadline - clock()
        if left <= 0:
            return False, (f"{why} after {timeout / 60:g} min. Re-run this release "
                           "once CI on the commit finishes.")
        print(f"{why}; checking again in {min(interval, left):g}s", flush=True)
        sleep(min(interval, left))


def main(argv: Optional[Sequence[str]] = None, *, run: Runner = _gh,
         sleep: Callable[[float], None] = time.sleep) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    ap.add_argument("--timeout-minutes", type=float, default=30)
    ap.add_argument("--interval", type=float, default=30, help="seconds between polls")
    args = ap.parse_args(argv)
    if not args.repo or not args.sha:
        ap.error("--repo and --sha are required outside GitHub Actions")
    ok, why = wait(args.repo, args.sha, run=run, timeout=args.timeout_minutes * 60,
                   interval=args.interval, sleep=sleep)
    print(why if ok else f"::error::{why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
