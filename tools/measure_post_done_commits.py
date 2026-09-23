"""Commits on a dispatched child's PR that the child did not make (#436).

Usage:
    PYTHONPATH=src python3 tools/measure_post_done_commits.py [--projects ~/.claude/projects] [--since YYYY-MM-DD] [--list] [--json]

Read-only: no LLM calls, no writes. It does read GitHub: one ``gh pr view``
per child PR.

A dispatched child stops itself once its PR is open. Whatever the PR needed
after that — a red check, a review, a conflict with its base — was fixed by
someone else, by hand. On 2026-09-22 a scratch count put that at 4 of 36
mnemo PRs and about 5 of 33 mnemo-desktop ones. #436 wakes the child to do it
instead; this counts whether that moved anything, per repository, so the
next round is read with the same ruler.

**The population** is every transcript in a dispatch worktree's project
directory — a cwd ending in ``-wt-<issue>`` or ``-wt-c-<piece>``, the only two
shapes ``mnemo dispatch`` creates — in which the child's own ``gh pr create``
printed a PR URL. Only that call's result counts: a child that ran
``gh pr list`` has every open PR's URL in its transcript, and taking the last
URL seen credited children with their siblings' PRs on the first run. A PR
``mnemo deliver`` opened for a child is therefore outside the population —
deliver runs in the parent — which since 2026-09-16, when ``--may pr``
became the default, is the exception. Two transcripts naming one PR count
once, as the one that ran last.

**Three instants per child**, all read off its transcript:

- *first stop*: the last record before the first ``<mnemo-pr-follow`` turn,
  or the last record at all when the child was never woken for its PR;
- *last turn*: the last record at all.

**Each commit on the PR** (``gh pr view --json commits``) is then one of:

- ``before`` — committed by the first stop: the child's own work;
- ``by_child_later`` — after the first stop, by the child's last turn: work
  the child did after it was woken (by this feature, or by anyone attaching);
- ``after_child`` — committed after the child's last turn. Nothing of the
  child's was running, so someone else made it: the maintainer, the parent
  session, a bot updating the branch. This is the number #436 wants to fall.

It goes by ``authoredDate``, which is when the commit was first made, not
when it was pushed; a commit made by the child and pushed later still reads as
the child's, which is the right answer. Not ``committedDate``: the first run
over the real transcripts (2026-09-22) listed PRs whose *only* commit read as
made after the child's last turn — the child's own commit, rebased or amended
by someone later, which rewrites the committer date and keeps the author's.
The cost runs the other way and is smaller: a child's commit someone amended
with a fix of their own reads as the child's. :data:`SLACK_SECONDS` absorbs
the clock difference between the machine and GitHub.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from mnemo.core.sessions import report_card

#: The opening of the turn that wakes a child for its PR (``wake.PR_NUDGE_PREFIX``).
FOLLOW = "<mnemo-pr-follow"

#: A dispatch worktree's project directory: Claude Code names it after the
#: cwd with every separator a dash, so ``…/mnemo-wt-436`` is ``…-mnemo-wt-436``.
#: The same two shapes as ``dispatch._WT_RE``.
WORKTREE_DIR = re.compile(r"-wt-(\d+|c-[a-z0-9-]+)$")

#: Clock difference tolerated between a transcript and GitHub.
SLACK_SECONDS = 60.0

_REPO = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/\d+")
_PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")

Runner = Callable[[Sequence[str], Optional[str]], Tuple[int, str, str]]


def _epoch(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        # 3.8-3.10 fromisoformat does not take every fraction width.
        match = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", value)
        if not match:
            return None
        stamp = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def _user_text(record: Dict[str, Any]) -> str:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(b.get("text") or "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _created(record: Dict[str, Any], creating: set, found: List[str]) -> None:
    """Collect ``gh pr create`` calls and the URLs their results printed."""
    content = (record.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            command = str((block.get("input") or {}).get("command") or "")
            if "gh pr create" in command:
                creating.add(block.get("id"))
        elif block.get("type") == "tool_result" and block.get("tool_use_id") in creating:
            body = block.get("content")
            text = body if isinstance(body, str) else json.dumps(body)
            found.extend(_PR_URL.findall(text))


def child(path: str) -> Optional[Dict[str, Any]]:
    """The instants and the PR for one child transcript, or ``None``."""
    stamps: List[float] = []
    first_stop: Optional[float] = None
    wakes = 0
    creating: set = set()
    urls: List[str] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                stamp = _epoch(record.get("timestamp"))
                _created(record, creating, urls)
                if record.get("type") == "user" and _user_text(record).lstrip().startswith(FOLLOW):
                    wakes += 1
                    if first_stop is None:
                        first_stop = stamps[-1] if stamps else stamp
                if stamp is not None:
                    stamps.append(stamp)
    except OSError:
        return None
    if not stamps:
        return None
    if not urls:
        return None
    last = max(stamps)
    return {
        "transcript": path,
        "pr": urls[-1],
        "first_stop": first_stop if first_stop is not None else last,
        "last_turn": last,
        "wakes": wakes,
    }


def commits(url: str, *, run: Runner = report_card._run) -> Optional[List[float]]:
    """``authoredDate`` of every commit on *url*, or ``None``."""
    code, out, _ = run(["gh", "pr", "view", url, "--json", "commits"], None)
    if code != 0:
        return None
    try:
        rows = (json.loads(out or "{}") or {}).get("commits") or []
    except (ValueError, AttributeError):
        return None
    found = []
    for row in rows:
        stamp = _epoch(row.get("authoredDate")) if isinstance(row, dict) else None
        if stamp is not None:
            found.append(stamp)
    return found


def classify(row: Dict[str, Any], stamps: Sequence[float]) -> Dict[str, int]:
    out = {"before": 0, "by_child_later": 0, "after_child": 0}
    for stamp in stamps:
        if stamp <= row["first_stop"] + SLACK_SECONDS:
            out["before"] += 1
        elif stamp <= row["last_turn"] + SLACK_SECONDS:
            out["by_child_later"] += 1
        else:
            out["after_child"] += 1
    return out


def repo_of(url: str) -> str:
    match = _REPO.match(url or "")
    return match.group(1) if match else "?"


def children(projects: str, *, since: Optional[float] = None) -> List[Dict[str, Any]]:
    """One row per child PR, the latest transcript naming it."""
    by_pr: Dict[str, Dict[str, Any]] = {}
    for directory in sorted(glob.glob(os.path.join(projects, "*"))):
        if not WORKTREE_DIR.search(os.path.basename(directory.rstrip(os.sep))):
            continue
        for path in sorted(glob.glob(os.path.join(directory, "*.jsonl"))):
            row = child(path)
            if row is None or (since is not None and row["last_turn"] < since):
                continue
            kept = by_pr.get(row["pr"])
            if kept is None or row["last_turn"] > kept["last_turn"]:
                by_pr[row["pr"]] = row
    return sorted(by_pr.values(), key=lambda r: r["first_stop"])


def measure(projects: str, *, since: Optional[float] = None,
            run: Runner = report_card._run) -> Dict[str, Any]:
    rows = children(projects, since=since)
    repos: Dict[str, Dict[str, int]] = {}
    for row in rows:
        stamps = commits(row["pr"], run=run)
        row["repo"] = repo_of(row["pr"])
        bucket = repos.setdefault(row["repo"], {
            "prs": 0, "unreadable": 0, "with_after_child": 0,
            "after_child_commits": 0, "woken": 0, "with_by_child_later": 0,
        })
        bucket["prs"] += 1
        bucket["woken"] += 1 if row["wakes"] else 0
        if stamps is None:
            row["commits"] = None
            bucket["unreadable"] += 1
            continue
        row["commits"] = classify(row, stamps)
        bucket["with_after_child"] += 1 if row["commits"]["after_child"] else 0
        bucket["after_child_commits"] += row["commits"]["after_child"]
        bucket["with_by_child_later"] += 1 if row["commits"]["by_child_later"] else 0
    return {"repos": repos, "children": rows}


def format_report(report: Dict[str, Any], *, listing: bool = False) -> str:
    lines = []
    unreadable = 0
    for repo, b in sorted(report["repos"].items()):
        read = b["prs"] - b["unreadable"]
        unreadable += b["unreadable"]
        if not read:
            # Test and probe sessions run in dispatch-shaped directories and
            # quote made-up URLs; a repository none of whose PRs `gh` could
            # read is one of those, or one this account cannot see.
            continue
        lines.append(
            f"{repo}: {b['with_after_child']} of {read} PRs have commits made after "
            f"the child's last turn ({b['after_child_commits']} commits); "
            f"{b['woken']} children woken for their PR, "
            f"{b['with_by_child_later']} PRs got commits from the child after it "
            f"first stopped" + (f"; {b['unreadable']} PRs unreadable" if b["unreadable"] else "")
        )
    if unreadable:
        lines.append(f"({unreadable} PR URLs in child transcripts could not be read with gh)")
    if listing:
        lines.append("")
        for row in report["children"]:
            c = row.get("commits")
            counts = ("unreadable" if c is None else
                      f"before {c['before']:2}  later {c['by_child_later']:2}  "
                      f"after {c['after_child']:2}")
            stop = datetime.fromtimestamp(row["first_stop"], tz=timezone.utc)
            lines.append(f"{stop:%Y-%m-%d %H:%M}  wakes {row['wakes']}  {counts}  {row['pr']}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--since", default=None,
                        help="only children whose last turn is on or after this date (YYYY-MM-DD)")
    parser.add_argument("--list", action="store_true", help="print every child PR's counts")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    since = _epoch(f"{args.since}T00:00:00Z") if args.since else None
    report = measure(args.projects, since=since)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(format_report(report, listing=args.list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
