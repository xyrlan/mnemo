"""How many background sessions each Claude account dir holds, and what the
roster's blindness to all but one of them cost ``pr-follow`` (#640).

Usage:
    PYTHONPATH=src python3 tools/measure_account_roots.py [--since 2026-10-07] [--json]

Claude Code keeps ``jobs/`` under its config dir, and ``CLAUDE_CONFIG_DIR``
moves it; until #640 mnemo read ``~/.claude/jobs`` alone. Two counts:

- **Per config dir** — the dirs ``mnemo.core.sessions.accounts.roots()``
  names (run this from a session on the account in question, or after one has
  run there, so the dir is on file): job records, roster workers, and how
  many ids sit under more than one dir.
- **Follows closed as ``session-gone``** since ``--since``, from the vault's
  ``pr-follow.json``, split by where the child's job record is now: under a
  non-default dir (the roster could not see it — this bug), under the default
  dir, or nowhere (``claude rm``, or retention). A record under the default
  dir or nowhere is not evidence either way about the day the follow closed.

Paths are printed as ``default`` / ``account N``, never spelled: an account
dir's name carries an e-mail address.

Nothing is sent anywhere. It reads, with the one write any mnemo process
makes: run with ``CLAUDE_CONFIG_DIR`` set, ``accounts.roots()`` puts that dir
on file in the vault's ``.mnemo/claude-config-dirs.jsonl``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

DEFAULT_SINCE = "2026-10-07"


def _job_ids(jobs: Path) -> List[str]:
    try:
        return sorted(d.name for d in jobs.iterdir()
                      if d.is_dir() and (d / "state.json").is_file())
    except OSError:
        return []


def _workers(home: Path) -> Optional[int]:
    try:
        data = json.loads((home / "daemon" / "roster.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    workers = data.get("workers") if isinstance(data, dict) else None
    return len(workers) if isinstance(workers, dict) else None


def count_roots(roots: Sequence[Any], default_jobs: Path) -> Dict[str, Any]:
    """Jobs and roster workers per root, and the ids under more than one."""
    rows: List[Dict[str, Any]] = []
    where: Dict[str, List[str]] = {}
    account = 0
    for root in roots:
        if Path(root.jobs) == Path(default_jobs):
            label = "default"
        else:
            account += 1
            label = f"account {account}"
        ids = _job_ids(Path(root.jobs))
        for short_id in ids:
            where.setdefault(short_id, []).append(label)
        rows.append({"root": label, "jobs": len(ids), "roster_workers": _workers(Path(root.home))})
    shared = sorted(i for i, labels in where.items() if len(labels) > 1)
    return {"roots": rows, "distinct_ids": len(where), "ids_under_two_roots": len(shared),
            "where": where}


def session_gone(ledger: Any, since: float, where: Dict[str, List[str]]) -> Dict[str, Any]:
    """The follows closed ``session-gone`` at or after *since*, by where the job is now."""
    children = ledger.get("children") if isinstance(ledger, dict) else None
    out = {"total": 0, "job_under_non_default": 0, "job_under_default": 0, "job_nowhere": 0}
    for short_id, entry in (children if isinstance(children, dict) else {}).items():
        if not isinstance(entry, dict) or entry.get("closed") != "session-gone":
            continue
        closed_at = entry.get("closed_at")
        if not isinstance(closed_at, (int, float)) or closed_at < since:
            continue
        out["total"] += 1
        labels = where.get(str(short_id), [])
        if any(label != "default" for label in labels):
            out["job_under_non_default"] += 1
        elif labels:
            out["job_under_default"] += 1
        else:
            out["job_nowhere"] += 1
    return out


def render(report: Dict[str, Any]) -> str:
    lines = [f"{'config dir':12s} {'jobs':>6s} {'roster workers':>15s}"]
    for row in report["roots"]:
        workers = "no roster" if row["roster_workers"] is None else str(row["roster_workers"])
        lines.append(f"{row['root']:12s} {row['jobs']:6d} {workers:>15s}")
    lines.append(f"distinct ids {report['distinct_ids']}, "
                 f"under two dirs {report['ids_under_two_roots']}")
    gone = report["session_gone"]
    lines.append(
        f"follows closed session-gone since {report['since']}: {gone['total']} "
        f"(job now under a non-default dir {gone['job_under_non_default']}, "
        f"under the default {gone['job_under_default']}, nowhere {gone['job_nowhere']})")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default=None, help="the vault (default: mnemo's configured one)")
    ap.add_argument("--since", default=DEFAULT_SINCE, help="YYYY-MM-DD, local midnight")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from mnemo.core.sessions import accounts, jobs

    if args.vault:
        vault = Path(args.vault).expanduser()
    else:
        from mnemo import cli
        vault = cli._resolve_vault()
    since = _dt.datetime.strptime(args.since, "%Y-%m-%d").timestamp()
    try:
        ledger = json.loads((vault / ".mnemo" / "pr-follow.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}

    report = count_roots(accounts.roots(vault_root=vault), jobs.jobs_dir())
    report["since"] = args.since
    report["session_gone"] = session_gone(ledger, since, report.pop("where"))
    prov = _provenance.provenance(__file__, argv, vault=vault)
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=1))
    else:
        print(_provenance.line(prov))
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
