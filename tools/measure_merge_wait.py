"""How long a dispatched child's green PR waited for its merge, and who merged it (#638).

Usage:
    PYTHONPATH=src python3 tools/measure_merge_wait.py [--repo OWNER/NAME ...]
        [--limit 100] [--vault ~/mnemo] [--prefix fix/issue-] [--list] [--json]

Read-only: no LLM calls, no writes. One ``gh pr list`` per repository.

A dispatched child ends with a PR, and the PR then waits for somebody to
merge it. This reads, per repository, the last ``--limit`` merged PRs and
keeps the ones whose branch a child's dispatch names (``--prefix``,
``fix/issue-`` by default). ``--repo`` takes several repositories, after one
flag or repeated; with more than one, the report closes on a total over the
ones ``gh`` could list. For each PR:

- *checks*: ``green`` when at least one check is reported and every one of
  them succeeded, ``none`` when no check is reported, ``not-green`` otherwise.
  A check counts as green on ``SUCCESS`` alone, which is what
  ``gh pr checks`` buckets as ``pass`` and what the ``pr-follow`` watcher
  holds a merge to (``pr_follow.merge_ready``): skipped, neutral, cancelled
  and pending are not green;
- *wait*: minutes from the last check completing to the merge. Negative when
  the PR merged before its last check completed; those are counted apart
  (``merged_early``) and left out of the percentiles and the sum;
- *who merged*: ``mnemo`` when the vault's ``.mnemo/pr-follow.json`` holds a
  follow for that PR closed with ``merged_by: "mnemo"`` — the watcher's own
  ``gh pr merge`` was accepted for the head that merged — else ``person``,
  which is everything else: a maintainer's ``gh pr merge``, ``mnemo land``,
  the web button. GitHub's ``mergedBy`` cannot tell them apart: the watcher
  merges with the maintainer's own ``gh`` login.

Percentiles are linear interpolation between closest ranks.

What it cannot see is printed as ``blind_spots``: the check times are the
rollup as it reads today, so a check rerun after the merge moves its
``completedAt`` past the merge and the PR reads as merged early; and only
follows on this machine's ledger can be credited to the watcher.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mnemo.core.sessions import report_card  # noqa: E402

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

Runner = Callable[[Sequence[str], Optional[str]], Tuple[int, str, str]]

#: The branches ``mnemo dispatch`` names (``dispatch.branch_name``).
PREFIX = "fix/issue-"
FIELDS = "number,url,createdAt,mergedAt,headRefName,statusCheckRollup"

MNEMO, PERSON = "mnemo", "person"
GREEN, NOT_GREEN, NONE = "green", "not-green", "none"


def _epoch(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def check_facts(rollup: Any) -> Tuple[str, Optional[float]]:
    """``(green | not-green | none, when the last check completed)`` for one
    PR's ``statusCheckRollup``. A check run carries ``conclusion`` and
    ``completedAt``; a commit status carries ``state`` and ``startedAt``,
    the only time it has."""
    rows = [r for r in (rollup or []) if isinstance(r, dict)]
    if not rows:
        return NONE, None
    green = True
    last: Optional[float] = None
    for r in rows:
        verdict = r.get("conclusion") if "conclusion" in r else r.get("state")
        if str(verdict or "").upper() != "SUCCESS":
            green = False
        at = _epoch(r.get("completedAt")) or _epoch(r.get("startedAt"))
        if at is not None and (last is None or at > last):
            last = at
    return (GREEN if green else NOT_GREEN), last


def load_merged_by_mnemo(vault: Path) -> set:
    """PR URLs whose follow on *vault*'s ledger closed as merged by mnemo."""
    try:
        data = json.loads((vault / ".mnemo" / "pr-follow.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    children = data.get("children") if isinstance(data, dict) else None
    if not isinstance(children, dict):
        return set()
    return {str(e["pr"]) for e in children.values()
            if isinstance(e, dict) and e.get("pr") and e.get("closed") == "merged"
            and e.get("merged_by") == MNEMO}


def row_of(repo: str, pr: Dict[str, Any], by_mnemo: set) -> Dict[str, Any]:
    """One merged PR as the report counts it."""
    checks, last = check_facts(pr.get("statusCheckRollup"))
    merged = _epoch(pr.get("mergedAt"))
    wait = (merged - last) / 60.0 if merged is not None and last is not None else None
    return {
        "repo": repo, "number": pr.get("number"), "url": pr.get("url"),
        "branch": str(pr.get("headRefName") or ""), "merged_at": pr.get("mergedAt"),
        "checks": checks, "wait_minutes": wait,
        "merged_by": MNEMO if pr.get("url") in by_mnemo else PERSON,
    }


def read_repo(repo: str, *, limit: int, run: Runner) -> Optional[List[Dict[str, Any]]]:
    """The last *limit* merged PRs of *repo*, or ``None`` when ``gh`` failed."""
    code, out, _ = run(["gh", "pr", "list", "-R", repo, "--state", "merged",
                        "--limit", str(limit), "--json", FIELDS], None)
    if code != 0:
        return None
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else None


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """The *q* (0-1) percentile of *values*, linear between closest ranks."""
    if not values:
        return None
    ordered = sorted(values)
    spot = (len(ordered) - 1) * q
    low = int(spot)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (spot - low)


def waits(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The wait over *rows*: those with a non-negative gap."""
    found = [r["wait_minutes"] for r in rows
             if r["wait_minutes"] is not None and r["wait_minutes"] >= 0]
    return {
        "n": len(found),
        "median": percentile(found, 0.5), "p75": percentile(found, 0.75),
        "p90": percentile(found, 0.9), "max": max(found) if found else None,
        "sum_hours": sum(found) / 60.0,
    }


def summarize(rows: Sequence[Dict[str, Any]], *, read: int,
              prefix: str = PREFIX) -> Dict[str, Any]:
    """One repository's report. *rows* is every merged PR read (*read* of them)."""
    children = [r for r in rows if r["branch"].startswith(prefix)]
    green = [r for r in children if r["checks"] == GREEN]
    early = [r for r in green if r["wait_minutes"] is not None and r["wait_minutes"] < 0]
    merged = [r["merged_at"] for r in children if r["merged_at"]]
    out: Dict[str, Any] = {
        "merged_read": read,
        "child_prs": len(children),
        "numbers": [min(r["number"] for r in children), max(r["number"] for r in children)]
        if children else None,
        "merged_from": min(merged) if merged else None,
        "merged_to": max(merged) if merged else None,
        "checks": {k: sum(1 for r in children if r["checks"] == k)
                   for k in (GREEN, NOT_GREEN, NONE)},
        "wait_minutes": waits(green),
        "merged_early": sorted(r["number"] for r in early),
        "merged_early_minutes": sorted(round(-r["wait_minutes"], 1) for r in early),
        "merged_by": {},
    }
    for who in (MNEMO, PERSON):
        mine = [r for r in green if r["merged_by"] == who]
        out["merged_by"][who] = {"prs": len(mine), "wait_minutes": waits(mine)}
    return out


def measure(repos: Sequence[str], *, limit: int, vault: Path, run: Runner,
            prefix: str = PREFIX) -> Dict[str, Any]:
    by_mnemo = load_merged_by_mnemo(vault)
    report: Dict[str, Any] = {"prefix": prefix, "limit": limit, "repos": {}, "rows": [],
                              "total": None}
    read = 0
    every: List[Dict[str, Any]] = []
    for repo in repos:
        prs = read_repo(repo, limit=limit, run=run)
        if prs is None:
            report["repos"][repo] = None
            continue
        rows = [row_of(repo, pr, by_mnemo) for pr in prs]
        report["repos"][repo] = summarize(rows, read=len(prs), prefix=prefix)
        report["rows"].extend(r for r in rows if r["branch"].startswith(prefix))
        read += len(prs)
        every.extend(rows)
    if len(report["repos"]) > 1:
        total = summarize(every, read=read, prefix=prefix)
        # PR numbers are per repository: a span across them names no PR,
        # and an early merge is named with its repository.
        total["numbers"] = None
        total["merged_early"] = sorted(
            f"{r['repo']}#{r['number']}" for r in report["rows"]
            if r["checks"] == GREEN and r["wait_minutes"] is not None
            and r["wait_minutes"] < 0)
        total["repos_read"] = sum(1 for s in report["repos"].values() if s is not None)
        report["total"] = total
    return report


def _n(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.0f}"


def _wait_line(w: Dict[str, Any]) -> str:
    if not w["n"]:
        return "no PR with a non-negative gap"
    return (f"median {_n(w['median'])} min, p75 {_n(w['p75'])}, p90 {_n(w['p90'])}, "
            f"max {_n(w['max'])}, {w['sum_hours']:.0f} h summed over {w['n']}")


def _block(name: str, s: Dict[str, Any], prefix: str, *, last: str) -> List[str]:
    """One repository's lines, or the total's under *name*."""
    span = ""
    if s["numbers"] or s["merged_from"]:
        parts = []
        if s["numbers"]:
            parts.append(f"#{s['numbers'][0]} to #{s['numbers'][1]}")
        if s["merged_from"]:
            parts.append(f"merged {str(s['merged_from'])[:10]} to {str(s['merged_to'])[:10]}")
        span = f" ({', '.join(parts)})"
    c = s["checks"]
    lines = [f"{name}: {s['child_prs']} of {last} {s['merged_read']} merged PRs "
             f"came from a `{prefix}*` branch{span}"]
    lines.append(f"  checks at merge, as the rollup reads today: {c[GREEN]} all green, "
                 f"{c[NOT_GREEN]} not, {c[NONE]} with none reported")
    lines.append(f"  last check completing -> merge, green PRs: {_wait_line(s['wait_minutes'])}")
    early = ", ".join(n if isinstance(n, str) else f"#{n}" for n in s["merged_early"]) or "none"
    mins = s["merged_early_minutes"]
    lines.append(f"  merged before the last check completed: {len(s['merged_early'])} ({early})"
                 + (f", {mins[0]:.0f} to {mins[-1]:.0f} min before" if mins else ""))
    for who, label in ((MNEMO, "the watcher"), (PERSON, "a person")):
        m = s["merged_by"][who]
        lines.append(f"  merged by {label}: {m['prs']}"
                     + (f" — {_wait_line(m['wait_minutes'])}" if m["prs"] else ""))
    return lines


def format_report(report: Dict[str, Any], *, listing: bool = False) -> str:
    lines: List[str] = []
    for repo, s in report["repos"].items():
        if s is None:
            lines.append(f"{repo}: gh could not list its merged PRs")
            continue
        lines.extend(_block(repo, s, report["prefix"], last="the last"))
    total = report.get("total")
    if total is not None:
        lines.append("")
        lines.extend(_block(f"total over {total['repos_read']} of {len(report['repos'])} "
                            "repositories", total, report["prefix"], last="the"))
    if listing:
        lines.append("")
        for r in report["rows"]:
            wait = "—" if r["wait_minutes"] is None else f"{r['wait_minutes']:.0f}"
            lines.append(f"  {r['repo']}#{r['number']}  {r['checks']:<9}  wait {wait:>5} min  "
                         f"{r['merged_by']}  {r['branch']}")
    return "\n".join(lines) + "\n"


def default_repo(run: Runner) -> Optional[str]:
    code, out, _ = run(["gh", "repo", "view", "--json", "nameWithOwner",
                        "--jq", ".nameWithOwner"], None)
    return out.strip() or None if code == 0 else None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--repo", action="extend", nargs="+", metavar="OWNER/NAME",
                        help="repositories to read, after one --repo or repeated "
                             "(default: the one gh resolves from the current directory)")
    parser.add_argument("--limit", type=int, default=100,
                        help="merged PRs to read per repository (default 100)")
    parser.add_argument("--vault", default=os.path.expanduser("~/mnemo"),
                        help="the vault whose pr-follow ledger says which merges were mnemo's")
    parser.add_argument("--prefix", default=PREFIX,
                        help=f"branch prefix of a dispatched child's PR (default {PREFIX})")
    parser.add_argument("--list", action="store_true", help="one line per child PR")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    run = report_card._run
    repos = args.repo or [r for r in [default_repo(run)] if r]
    if not repos:
        sys.stderr.write("no repository: pass --repo OWNER/NAME\n")
        return 2
    vault = Path(args.vault).expanduser()
    report = measure(repos, limit=args.limit, vault=vault, run=run, prefix=args.prefix)
    prov = _provenance.provenance(__file__, argv, vault=vault, blind_spots=[
        "check times are the rollup as it reads today: a check rerun after the merge "
        "reads as a PR merged early",
        "who merged is read from this vault's pr-follow ledger: a merge the watcher "
        "made from another machine counts as a person's",
        "PRs closed without a merge are not read",
    ])
    if args.json:
        sys.stdout.write(json.dumps(_provenance.stamp(report, prov), indent=2, default=str) + "\n")
    else:
        sys.stdout.write(_provenance.line(prov) + "\n"
                         + format_report(report, listing=args.list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
