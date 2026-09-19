"""How many procedures children keep rediscovering, and how many are already written down (#392).

Usage:
    PYTHONPATH=src python3 tools/measure_rediscovered_procedures.py [--projects ~/.claude/projects]
    PYTHONPATH=src python3 tools/measure_rediscovered_procedures.py --rejected
    PYTHONPATH=src python3 tools/measure_rediscovered_procedures.py --json

Read-only: no LLM calls, no writes, no behaviour change.

The question #392 gates itself on: *across the transcripts on disk, how many
distinct procedures were rediscovered by two or more children of the same
repo, and how many of those does that repo's ``CLAUDE.md`` already state?* If
the answer were "the handful PR #387 wrote by hand", nothing should ship.

Where ``tools/measure_child_procedures.py`` (#385) asks a question someone
already knew the answer to — "did this child run ``pytest`` with
``PYTHONPATH``" — this one asks the transcript what children worked out, with
no probe written down anywhere. The detection lives in
:mod:`mnemo.core.procedures`, which ``mnemo procedures`` also serves; this
prints the population-level count that the command's per-repo listing does
not.

``--rejected`` prints the other half of the finding: the same bar applied to
**flags** instead of environment assignments. A procedure can live in a flag —
clubinho's ``--runInBand`` is one — but in a transcript a flag a child adopts
looks exactly like a flag a child chose, and the count says how badly: on
2026-09-19, twelve children each "rediscovered" ``cargo test --nocapture``,
which is a way of reading output and not a boundary at all. So the command
proposes from the environment only, and the flags are counted rather than
guessed at.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional

from mnemo.core import procedures as P


def measure(projects: str, *, min_children: int = 2, max_shape_repos: int = 2) -> Dict[str, Any]:
    """The population, the candidates, and what each repo's file already says."""
    transcripts = P.dispatch_transcripts(projects)
    children: Dict[str, int] = {}
    for _path, cwd in transcripts:
        repo = P.fold_repo(os.path.basename(cwd.rstrip("/")))
        children[repo] = children.get(repo, 0) + 1

    def rows(kind: str) -> List[Dict[str, Any]]:
        out = []
        for candidate in P.scan(projects, min_children=min_children,
                                max_shape_repos=max_shape_repos, kind=kind):
            out.append({
                "repo": candidate.repo,
                "key": candidate.key,
                "shape": candidate.shape,
                "children": len(candidate.rediscovered),
                "shape_children": candidate.shape_children,
                "stated": candidate.stated,
                "has_claude_md": bool(P.read_claude_md(candidate.repo_root)),
                "carriers": [
                    {"name": c.name, "rediscovered": len(c.rediscovered), "kept": c.kept,
                     "never": c.never, "stated": c.stated, "values": len(c.values)}
                    for c in candidate.carriers
                ],
            })
        return out

    env = rows(P.ENV)
    return {
        "children": children,
        "transcripts": len(transcripts),
        "env": env,
        "flag": rows(P.FLAG),
        "silence": silence(projects, env),
    }


def silence(projects: str, rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """Bare runs of the candidate shapes, and how many the child could see fail.

    The other half of why this had to be measured rather than noticed: a run
    missing what the repo requires mostly *succeeds*. A bare ``pytest`` in a
    mnemo worktree prints ``83 passed`` against the main checkout's ``src``.
    Detection that waited for an error would find almost none of this.
    """
    wanted: Dict[str, Dict[str, set]] = {}
    for row in rows:
        wanted.setdefault(row["repo"], {})[row["shape"]] = {
            c["name"] for c in row["carriers"]}

    bare = errors = 0
    for path, cwd in P.dispatch_transcripts(projects):
        repo = P.fold_repo(os.path.basename(cwd.rstrip("/")))
        shapes = wanted.get(repo)
        if not shapes:
            continue
        pending = set()
        for record in P._records(path):
            for block in P._blocks(record):
                kind = block.get("type")
                if kind == "tool_use" and block.get("name") == "Bash":
                    command = (block.get("input") or {}).get("command")
                    if not isinstance(command, str):
                        continue
                    if any(shape in shapes and (shapes[shape] - set(env))
                           for shape, env, _flags in P.invocations(command)):
                        pending.add(block.get("id"))
                elif kind == "tool_result" and block.get("tool_use_id") in pending:
                    pending.discard(block.get("tool_use_id"))
                    bare += 1
                    errors += bool(block.get("is_error"))
    return {"bare_runs": bare, "errors": errors}


def _carriers(row: Dict[str, Any]) -> str:
    return ", ".join(
        f"{c['name']}{'' if c['stated'] else ' *'}" for c in row["carriers"])


def format_report(report: Dict[str, Any], *, rejected: bool = False) -> str:
    lines: List[str] = []
    total = report["transcripts"]
    lines.append(f"{total} dispatch children on disk, "
                 f"{len(report['children'])} repos")
    for repo, count in sorted(report["children"].items(), key=lambda kv: -kv[1]):
        lines.append(f"    {repo:20} {count:4} children")
    lines.append("")

    env = report["env"]
    stated = [r for r in env if r["stated"]]
    lines.append(f"{len(env)} procedure(s) rediscovered by >= 2 children of one repo; "
                 f"{len(stated)} already stated in that repo's CLAUDE.md")
    for row in env:
        mark = "stated " if row["stated"] else "PROPOSE"
        lines.append(f"    {row['repo']:16} {row['key']:18} {mark}  "
                     f"{row['children']:3}/{row['shape_children']:<3} children   "
                     f"{_carriers(row)}")
    lines.append("")
    lines.append("    (* = this carrier is not in the repo's CLAUDE.md)")
    quiet = report.get("silence") or {}
    if quiet.get("bare_runs"):
        lines.append(f"    {quiet['bare_runs']} runs of those shapes were missing one, and "
                     f"{quiet['errors']} of them failed in a way the child could see —")
        lines.append("    the state that costs is the silent one, which is why it is counted")
        lines.append("    here and not waited for.")
    lines.append("")

    if rejected:
        flags = report["flag"]
        lines.append(f"rejected: the same bar over flags instead of the environment — "
                     f"{len(flags)} would qualify")
        for row in sorted(flags, key=lambda r: -r["children"])[:25]:
            lines.append(f"    {row['repo']:16} {row['key']:18} "
                         f"{row['children']:3}/{row['shape_children']:<3} children   "
                         f"{_carriers(row)[:70]}")
        lines.append("")
        lines.append("    A flag a child adopts and a flag a child chose are the same event")
        lines.append("    in a transcript, so none of these is proposed. What that costs is")
        lines.append("    the procedures that do live in a flag — clubinho's --runInBand.")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--min-children", type=int, default=2)
    parser.add_argument("--max-shape-repos", type=int, default=2)
    parser.add_argument("--rejected", action="store_true",
                        help="also print what the same bar over flags would have proposed")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = measure(args.projects, min_children=args.min_children,
                     max_shape_repos=args.max_shape_repos)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report, rejected=args.rejected), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
