"""Do a dispatch's children collide, and does what the parent wrote reach them (#384).

Usage:
    PYTHONPATH=src python3 tools/measure_dispatch_siblings.py \
        [--projects ~/.claude/projects] [--vault ~/mnemo] [--list] [--json]

Read-only: no LLM calls, no writes, no behaviour change.

#384 asks whether the dispatching session's decisions reach the child, and
names three that might not: what was ruled out, what was measured, and which
sibling is doing what. This measures the last one, and the channel the first
two already have.

**The population.** Every line of ``<vault>/.mnemo/dispatch-parents.jsonl``
(#288) whose child still has a transcript on disk. That log is the only record
of who dispatched whom — the worktree path names the issue, never the parent —
and it is what makes a *batch* recoverable at all.

**What a batch is.** Children of the same parent whose first turns fall within
``--window`` minutes of each other, chained. A parent that dispatches four
issues at 10:00 and three more at 14:00 ran two batches, and a child in the
first has no sibling in the second: the roster #384 is about is what was live
while the child ran, not what that session ever started.

**The two numbers.**

- *Collisions.* Two children of one batch that wrote to the same repo-relative
  path. Edits are read from the child's own transcript (``Edit``/``Write``),
  not from git, so a child whose branch was deleted or never merged still
  counts. This is an upper bound on harm and not a conflict: two children
  editing one file at opposite ends merge cleanly, and ``--list`` prints every
  pair so a number is never quoted without them.
- *Reach.* Whether the child ran ``gh issue view`` on anything. The parent's
  written decisions travel in the issue body and its comments, and the opening
  prompt tells the child to read them; this is whether it did.

The split that matters is by kind. A contract piece is already told which files
are not its own; an issue child is told nothing about the others at all. If the
collision rate is the same on both sides, the roster is not what prevents them.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import glob
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: Anything that looks like an edit to a file inside the child's own worktree.
_WT_PATH = re.compile(r"-wt-(?:\d+|c-[a-z0-9-]+|[a-z0-9]+-wt-\d+)/(.*)$")
_EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_GH_VIEW = re.compile(r"gh\s+issue\s+view")

#: How a child's first turn reads when the opening prompt actually arrived.
#: One child in the measured population (`mnemo-wt-361`) has the single user
#: turn ``--setting-sources`` — the read-only argv bug fixed in #371 ate the
#: prompt — so it was never told to read anything. Counting it as a child that
#: ignored its issue would be scoring a bug this measurement is not about.
_OPENING = re.compile(r"^(?:Work on|Investigate) issue #\d+")


def _read_jsonl(path: str) -> List[dict]:
    out: List[dict] = []
    try:
        handle = open(path, encoding="utf-8")
    except OSError:
        return out
    with handle:
        for line in handle:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _parents(vault: str) -> Dict[str, str]:
    """``short_id -> parent_session``, the later line winning, as `parents.read`."""
    out: Dict[str, str] = {}
    for entry in _read_jsonl(os.path.join(vault, ".mnemo", "dispatch-parents.jsonl")):
        short_id, parent = entry.get("short_id"), entry.get("parent_session")
        if isinstance(short_id, str) and isinstance(parent, str) and short_id and parent:
            out[short_id] = parent
    return out


def _stamp(event: dict) -> Optional[datetime.datetime]:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _tool_uses(events: Iterable[dict]) -> Iterable[Tuple[str, dict]]:
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield block.get("name") or "", block.get("input") or {}


def _relative(path: str) -> str:
    """The path as the *repo* names it, so two worktrees' copies are one file."""
    match = _WT_PATH.search(path.replace("\\", "/"))
    return match.group(1) if match else path


def _opening(events: List[dict]) -> str:
    for event in events:
        if event.get("type") != "user":
            continue
        content = (event.get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text") or ""
        return ""
    return ""


def _child(transcript: str) -> Dict[str, Any]:
    events = _read_jsonl(transcript)
    started = next((s for s in (_stamp(e) for e in events) if s), None)
    cwd = next((e["cwd"] for e in events if isinstance(e.get("cwd"), str)), "")
    edits, read_issue = set(), False
    for name, args in _tool_uses(events):
        if not isinstance(args, dict):
            continue
        if name in _EDIT_TOOLS:
            target = args.get("file_path") or args.get("notebook_path")
            if isinstance(target, str):
                edits.add(_relative(target))
        elif name == "Bash" and isinstance(args.get("command"), str):
            read_issue = read_issue or bool(_GH_VIEW.search(args["command"]))
    return {
        "cwd": cwd,
        "name": cwd.rstrip("/").rsplit("/", 1)[-1],
        "started": started,
        "kind": "piece" if "-wt-c-" in cwd else "issue",
        "edits": sorted(edits),
        "read_issue": read_issue,
        "prompted": bool(_OPENING.match(_opening(events).strip())),
    }


def _transcripts(projects: str) -> Dict[str, str]:
    return {
        os.path.basename(path)[: -len(".jsonl")]: path
        for path in glob.glob(os.path.join(projects, "*", "*.jsonl"))
    }


def _batches(children: List[dict], window_minutes: float) -> List[List[dict]]:
    """Children grouped by parent, then cut wherever the gap exceeds the window."""
    ordered = sorted(children, key=lambda c: (c["parent"], c["started"]))
    out: List[List[dict]] = []
    for child in ordered:
        if (out and out[-1][0]["parent"] == child["parent"]
                and (child["started"] - out[-1][-1]["started"]).total_seconds()
                <= window_minutes * 60):
            out[-1].append(child)
        else:
            out.append([child])
    return out


def _collisions(batch: List[dict]) -> Dict[str, List[str]]:
    """``path -> the children that wrote it``, for paths more than one wrote."""
    who: Dict[str, List[str]] = collections.defaultdict(list)
    for child in batch:
        for path in child["edits"]:
            who[path].append(child["name"])
    return {path: names for path, names in who.items() if len(names) > 1}


def measure(projects: str, vault: str, *, window_minutes: float = 30.0) -> Dict[str, Any]:
    transcripts = _transcripts(projects)
    by_prefix: Dict[str, List[str]] = collections.defaultdict(list)
    for uuid in transcripts:
        by_prefix[uuid[:8]].append(uuid)

    children: List[dict] = []
    for short_id, parent in sorted(_parents(vault).items()):
        for uuid in by_prefix.get(short_id, []):
            row = _child(transcripts[uuid])
            if row["started"] is None:
                continue
            row.update(short_id=short_id, parent=parent, session=uuid)
            children.append(row)

    batches = _batches(children, window_minutes)
    rows = []
    for batch in batches:
        kinds = {child["kind"] for child in batch}
        collisions = _collisions(batch) if len(batch) > 1 else {}
        rows.append({
            "parent": batch[0]["parent"],
            "started": batch[0]["started"].isoformat(),
            "size": len(batch),
            "kind": kinds.pop() if len(kinds) == 1 else "mixed",
            "children": [child["name"] for child in batch],
            "collisions": {path: names for path, names in sorted(collisions.items())},
        })

    def _bucket(kind: str) -> Dict[str, int]:
        picked = [r for r in rows if r["size"] > 1 and r["kind"] == kind]
        return {
            "batches": len(picked),
            "children": sum(r["size"] for r in picked),
            "with_a_collision": sum(1 for r in picked if r["collisions"]),
            "files": sum(len(r["collisions"]) for r in picked),
        }

    issue_children = [c for c in children if c["kind"] == "issue"]
    prompted = [c for c in issue_children if c["prompted"]]
    return {
        "children": len(children),
        "batches": len(rows),
        "multi_child_batches": sum(1 for r in rows if r["size"] > 1),
        "children_with_a_sibling": sum(r["size"] for r in rows if r["size"] > 1),
        "by_kind": {"issue": _bucket("issue"), "piece": _bucket("piece")},
        "issue_children": len(issue_children),
        "issue_children_never_prompted": len(issue_children) - len(prompted),
        "issue_children_that_read_their_issue":
            sum(1 for c in prompted if c["read_issue"]),
        "rows": rows,
    }


def format_report(report: Dict[str, Any], *, listing: bool = False) -> str:
    lines = [
        f"{report['children']} dispatched children with a transcript, "
        f"in {report['batches']} batches.",
        f"{report['multi_child_batches']} batches started more than one child; "
        f"{report['children_with_a_sibling']} children had a live sibling.",
        "",
        "siblings of one batch writing the same file:",
    ]
    for kind, bucket in report["by_kind"].items():
        lines.append(
            f"  {kind:<6} {bucket['with_a_collision']}/{bucket['batches']} batches"
            f"  ({bucket['children']} children, {bucket['files']} files)"
        )
    reach = report["issue_children_that_read_their_issue"]
    lost = report["issue_children_never_prompted"]
    total = report["issue_children"] - lost
    lines += [
        "",
        f"issue children that ran `gh issue view`: {reach}/{total}"
        " — what the parent wrote down already reaches them.",
    ]
    if lost:
        lines.append(
            f"  ({lost} excluded: the opening prompt never arrived, so there was "
            "nothing telling them to read it)"
        )
    if listing:
        lines.append("")
        for row in report["rows"]:
            if not row["collisions"]:
                continue
            lines.append(f"  {row['started'][:16]}  {row['kind']}  n={row['size']}")
            for path, names in row["collisions"].items():
                lines.append(f"    {path}  <- {', '.join(names)}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--vault", default=os.path.expanduser("~/mnemo"))
    parser.add_argument("--window", type=float, default=30.0,
                        help="minutes between dispatches that still count as one batch")
    parser.add_argument("--list", action="store_true",
                        help="print every colliding pair; read it before quoting a rate")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = measure(args.projects, args.vault, window_minutes=args.window)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report, listing=args.list), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
