"""Replay briefing selection against the real vault: does a query beat newest-wins?

Usage:
    PYTHONPATH=src python3 tools/measure_briefing_query.py [--vault ~/mnemo] [--agent mnemo] [--json]

Read-only: no LLM calls, no writes. Reads briefing files only — no transcript.

At ``SessionStart`` the hook has no prompt; the only task signal it has is the
branch checked out in ``cwd``. This replays that query over every briefing the
vault holds for one project:

- **Case.** Each briefing ``s`` stands for the session that wrote it.
- **Pool.** The briefings that already existed when ``s`` started — file mtime
  earlier than ``s``'s mtime minus its ``duration_minutes`` — ordered the way
  the hook orders them, newest ``POOL_SIZE`` kept. ``pool[0]`` is what
  newest-wins injected.
- **Query.** The branch ``s`` names on its own ``Branch:`` line. That line is
  written at session end, so a session that branched mid-way leaks a branch
  the hook could not have seen: this is an upper bound on the branch signal.
- **Truth.** File basenames ``s`` mentions, overlapped with each pool member's.
  The best briefing is the one sharing the most; a case where no pool member
  shares any is not scored. File names are a different signal from the branch
  words the query ranks on, so the truth is not the query restated.

Reports hits@1 for newest-wins, for ``briefing_select.choose`` as the hook
would run it, ungated, and the expected hits of a uniform random pick.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import re
from pathlib import Path

from mnemo.core import briefing_select
from mnemo.core.briefing import BriefingRecord
from mnemo.core.extract.scanner import parse_frontmatter

_PATH_RE = re.compile(
    r"(?:[\w.-]+/)+[\w.-]+\.\w{1,5}"
    r"|\b[\w-]+\.(?:py|ts|tsx|rs|json|jsonl|toml|yml|yaml|sh)\b"
)
# Named in nearly every briefing that touches a release; overlap on them says nothing.
_GENERIC_FILES = frozenset({"CHANGELOG.md", "README.md"})
_BRANCH_RE = re.compile(r"^\s*\*{0,2}Branch:\*{0,2}\s*`?([\w./-]*\w)`?", re.M)
_UNGATED = {"absolute_floor": 0.0, "relative_gap": 1.0, "term_overlap_min": 1}


def files_named(body: str) -> set[str]:
    return {os.path.basename(p) for p in _PATH_RE.findall(body)} - _GENERIC_FILES


def branch_named(body: str) -> str:
    m = _BRANCH_RE.search(body)
    return m.group(1) if m else ""


def _order_key(rec: BriefingRecord) -> tuple:
    fm = rec.frontmatter
    return (1 if fm.get("date") else 0, fm.get("date", ""), fm.get("session_id", rec.path.stem))


def load(vault: Path, agent: str) -> list[tuple[float, BriefingRecord]]:
    d = vault / "bots" / agent / "briefings" / "sessions"
    out = []
    for md in sorted(d.glob("*.md")):
        fm, body = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
        out.append((md.stat().st_mtime, BriefingRecord(md, fm, body.lstrip("\n"))))
    return out


def measure(briefings: list[tuple[float, BriefingRecord]], *, seed: int = 0) -> dict:
    counts: collections.Counter = collections.Counter()
    reasons: collections.Counter = collections.Counter()
    rows = []
    for mtime, s in briefings:
        try:
            minutes = int(s.frontmatter.get("duration_minutes") or 0)
        except ValueError:
            minutes = 0
        started = mtime - 60 * minutes
        pool = sorted(
            (r for m, r in briefings if m < started and r is not s),
            key=_order_key, reverse=True,
        )[: briefing_select.POOL_SIZE]
        if len(pool) < 2:
            counts["pool_under_2"] += 1
            continue
        counts["cases"] += 1
        branch = branch_named(s.body)
        if not briefing_select.query_tokens(branch):
            counts["no_query"] += 1
            continue
        counts["with_query"] += 1
        mine = files_named(s.body)
        overlap = [len(mine & files_named(r.body)) for r in pool]
        best = max(overlap)
        if best == 0:
            counts["no_relevant_in_pool"] += 1
            continue
        counts["scored"] += 1
        gated = briefing_select.choose(pool, branch)
        ungated = briefing_select.choose(pool, branch, thresholds=_UNGATED)
        at = {id(r): i for i, r in enumerate(pool)}
        g, u = at[id(gated.record)], at[id(ungated.record)]
        reasons[gated.reason] += 1
        counts["hits_newest"] += overlap[0] == best
        counts["hits_gated"] += overlap[g] == best
        counts["hits_ungated"] += overlap[u] == best
        counts["expected_hits_random_x1000"] += round(
            1000 * sum(o == best for o in overlap) / len(overlap))
        if g != 0:
            delta = overlap[g] - overlap[0]
            counts["replaced_better" if delta > 0 else "replaced_worse" if delta < 0
                   else "replaced_same"] += 1
        rows.append({
            "session": s.frontmatter.get("session_id", s.path.stem),
            "branch": branch, "reason": gated.reason, "best": best,
            "newest": overlap[0], "gated": overlap[g], "ungated": overlap[u],
        })
    return {"counts": dict(counts), "reasons": dict(reasons), "rows": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="~/mnemo")
    ap.add_argument("--agent", default="mnemo")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = measure(load(Path(args.vault).expanduser(), args.agent))
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    c = result["counts"]
    for r in result["rows"]:
        print(f"  {r['branch']:<45} {r['reason']:<20} best={r['best']} "
              f"newest={r['newest']} chosen={r['gated']} ungated={r['ungated']}")
    scored = c.get("scored", 0)
    print(f"cases={c.get('cases', 0)} no_query={c.get('no_query', 0)} "
          f"with_query={c.get('with_query', 0)} scored={scored}")
    print(f"hits@1 newest={c.get('hits_newest', 0)}/{scored} "
          f"chosen={c.get('hits_gated', 0)}/{scored} "
          f"ungated={c.get('hits_ungated', 0)}/{scored} "
          f"random≈{c.get('expected_hits_random_x1000', 0) / 1000:.1f}/{scored}")
    print(f"replaced: better={c.get('replaced_better', 0)} same={c.get('replaced_same', 0)} "
          f"worse={c.get('replaced_worse', 0)}  reasons={result['reasons']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
