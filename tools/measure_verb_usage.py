"""Which `mnemo` verbs are actually run outside mnemo's own repos (#437).

Usage:
    PYTHONPATH=src python3 tools/measure_verb_usage.py \
        [--projects ~/.claude/projects] [--days 30] [--list] [--json]

Read-only: no LLM calls, no writes, no behaviour change.

#437 is about `mnemo --help` listing 47 subcommands under a tagline the
README contradicts. The number that motivates trimming what the first
screen shows is which verbs a user actually types (or a session actually
runs on their behalf) *outside mnemo's own repos* — inside them, every verb
is exercised on purpose by mnemo's own tests and dogfooding, which would
swamp the six or so verbs an ordinary user reaches for.

**What this reads.** Every ``*.jsonl`` transcript under ``--projects``,
excluding:

- projects whose cwd contains ``mnemo`` (mnemo and mnemo-desktop, and any
  worktree of either) — developing mnemo is not using it;
- throwaway cwds (:func:`mnemo.core.hook_guard.is_throwaway`) — a test
  fixture or a background job's scratch probe, not a real session.

Within what's left, two channels count as a call to ``mnemo <verb>``:

- a ``Bash`` tool_use whose command matches ``mnemo <verb>`` — Claude running
  it on the user's behalf;
- a ``<bash-input>...</bash-input>`` user turn matching the same pattern —
  the human typing ``!mnemo <verb>`` themselves.

A command containing ``git commit``, ``gh pr`` or ``gh issue`` is skipped
entirely before matching: prose in a commit message or issue body ("run
`mnemo doctor` first") reads as a call otherwise, and mnemo's own history is
full of exactly that prose.

**The regex.** ``(^|[;&|(\\s])mnemo\\s+([a-z][a-z-]+)`` — the verb is
whatever token follows ``mnemo``, anchored so ``mnemo-desktop`` or a
variable named ``...mnemo`` never matches.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from mnemo.core.hook_guard import is_throwaway  # noqa: E402

#: The verb token after ``mnemo``, anchored so ``mnemo-desktop`` (a
#: different word starting with the same six letters) never matches, and a
#: shell operator or open-paren before ``mnemo`` still counts it as a call.
VERB_RE = re.compile(r"(^|[;&|(\s])mnemo\s+([a-z][a-z-]+)")

#: A command whose prose, not its argv, is what matches VERB_RE — mnemo's own
#: commit and issue history routinely says "run `mnemo doctor`".
_EXCLUDE_CMD_RE = re.compile(r"git\s+commit|gh\s+pr|gh\s+issue")

_BASH_INPUT_RE = re.compile(r"<bash-input>(.*?)</bash-input>", re.I | re.S)


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


def _stamp(event: dict) -> Optional[datetime.datetime]:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    return ts


def _cwd(events: List[dict]) -> str:
    return next((e["cwd"] for e in events if isinstance(e.get("cwd"), str)), "")


def _is_mnemo_project(cwd: str) -> bool:
    return "mnemo" in cwd.lower()


def _bash_commands(events: Iterable[dict]) -> Iterable[Tuple[dict, str]]:
    """``(event, command)`` for every Bash tool_use in an assistant turn."""
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") != "Bash":
                continue
            command = (block.get("input") or {}).get("command")
            if isinstance(command, str):
                yield event, command


def _bash_input_commands(events: Iterable[dict]) -> Iterable[Tuple[dict, str]]:
    """``(event, command)`` for every ``<bash-input>`` block in a user turn —
    a ``!``-prefixed shell command the human typed directly."""
    for event in events:
        if event.get("type") != "user":
            continue
        content = (event.get("message") or {}).get("content")
        text = content if isinstance(content, str) else None
        if text is None and isinstance(content, list):
            parts = [b.get("text") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            text = "\n".join(p for p in parts if isinstance(p, str))
        if not text:
            continue
        for match in _BASH_INPUT_RE.finditer(text):
            yield event, match.group(1).strip()


def _verbs(command: str) -> List[str]:
    if _EXCLUDE_CMD_RE.search(command):
        return []
    return [m.group(2) for m in VERB_RE.finditer(command)]


def _transcript_calls(
    events: List[dict], *, since: Optional[datetime.datetime],
) -> Dict[str, Dict[str, int]]:
    """``verb -> {"tool": n, "typed": n}`` for one transcript's events."""
    counts: Dict[str, Dict[str, int]] = collections.defaultdict(
        lambda: {"tool": 0, "typed": 0})
    for kind, source in (("tool", _bash_commands), ("typed", _bash_input_commands)):
        for event, command in source(events):
            if since is not None:
                ts = _stamp(event)
                if ts is not None and ts < since:
                    continue
            for verb in _verbs(command):
                counts[verb][kind] += 1
    return counts


def _transcripts(projects: str) -> List[str]:
    return glob.glob(os.path.join(projects, "*", "*.jsonl"))


def measure(
    projects: str, *, since_days: Optional[float] = 30.0,
) -> Dict[str, Any]:
    since = None
    if since_days is not None:
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=since_days)

    total = 0
    excluded_mnemo = 0
    excluded_temp = 0
    counted = 0
    by_verb: Dict[str, Dict[str, int]] = collections.defaultdict(
        lambda: {"tool": 0, "typed": 0})
    by_project: Dict[str, Dict[str, int]] = collections.defaultdict(
        lambda: {"tool": 0, "typed": 0})

    for path in _transcripts(projects):
        total += 1
        events = _read_jsonl(path)
        cwd = _cwd(events)
        if _is_mnemo_project(cwd):
            excluded_mnemo += 1
            continue
        if is_throwaway(cwd):
            excluded_temp += 1
            continue
        counted += 1
        for verb, kinds in _transcript_calls(events, since=since).items():
            for kind, n in kinds.items():
                by_verb[verb][kind] += n
                by_project[cwd][kind] += n

    total_calls = {
        verb: kinds["tool"] + kinds["typed"] for verb, kinds in by_verb.items()
    }
    ordered = sorted(total_calls.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "transcripts": total,
        "excluded_mnemo_repos": excluded_mnemo,
        "excluded_temp": excluded_temp,
        "counted": counted,
        "since_days": since_days,
        "verbs": [
            {"verb": verb, "calls": total_calls[verb], **by_verb[verb]}
            for verb, _ in ordered
        ],
        "typed_calls": sum(v["typed"] for v in by_verb.values()),
        "tool_calls": sum(v["tool"] for v in by_verb.values()),
    }


def format_report(report: Dict[str, Any], *, top: int = 6, listing: bool = False) -> str:
    lines = [
        f"{report['counted']} transcripts outside mnemo's own repos "
        f"({report['excluded_mnemo_repos']} excluded as mnemo/mnemo-desktop, "
        f"{report['excluded_temp']} excluded as throwaway), "
        f"last {report['since_days']} days." if report["since_days"] is not None
        else f"{report['counted']} transcripts outside mnemo's own repos "
             f"({report['excluded_mnemo_repos']} excluded as mnemo/mnemo-desktop, "
             f"{report['excluded_temp']} excluded as throwaway).",
        "",
    ]
    verbs = report["verbs"]
    for row in verbs[:top]:
        lines.append(f"  {row['verb']:<20} {row['calls']:>4}")
    rest = verbs[top:]
    if rest:
        lines.append(
            f"  {'every other verb':<20} {max((r['calls'] for r in rest), default=0):>4} "
            f"or fewer ({len(rest)} verbs)"
        )
    lines += [
        "",
        f"human-typed (!mnemo): {report['typed_calls']}   "
        f"Claude-run (Bash tool_use): {report['tool_calls']}",
    ]
    if listing:
        lines.append("")
        for row in verbs:
            lines.append(f"  {row['verb']:<20} tool={row['tool']:<4} typed={row['typed']}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument(
        "--days", type=float, default=30.0,
        help="only calls in the last N days (default 30; 0 disables the window)",
    )
    parser.add_argument("--list", action="store_true",
                        help="print every verb's tool/typed split, not just the top ones")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    since_days = None if args.days == 0 else args.days
    report = measure(args.projects, since_days=since_days)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report, listing=args.list), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
