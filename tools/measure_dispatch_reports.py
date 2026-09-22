"""How often a session repeats dispatch's queue hint back as its own plan (#306).

Usage:
    PYTHONPATH=src python3 tools/measure_dispatch_reports.py [--projects ~/.claude/projects] [--list] [--json]

Read-only: no LLM calls, no writes, no behaviour change.

``mnemo dispatch`` ends with ``queue:  mnemo sessions``. Run from a session's
Bash tool, that footer is read by the model, which then told the maintainer
"Acompanho com `mnemo sessions`… te aviso quando terminarem" — a promise
nothing would wake it to keep. Since #306 a session also gets a note saying
so. This counts whether the note moved anything.

The issue proposed counting every assistant turn that mentions ``mnemo
sessions``. Measured on 2026-09-15 that is 142 turns, and 113 of them are in
the mnemo repo or its subagents — the maintainer building the command, not a
session misreporting it. That count tracks development work and cannot show
this footer's effect, so the population here is narrower:

- **A dispatch report** is the assistant text between a real dispatch and the
  next human prompt. A real dispatch is a Bash call whose command runs
  ``mnemo dispatch`` and whose output carries the footer line — which only
  prints once a child started, so a dry run, a refusal and a ``cat`` of
  ``dispatch.py`` (whose source holds the same string) all fall out.
- **Mentions** is whether that report names ``mnemo sessions``. Handing the
  command to the maintainer is not wrong in itself, which is why it is not
  the headline.
- **Unbacked** is a mention in a turn that armed nothing able to wake the
  session afterwards: no ``Monitor``, no ``ScheduleWakeup``, no Bash call run
  in the background. The maintainer's own dispatch rounds arm a watcher and
  keep the promise; those are counted as backed rather than as the failure.
  So is a turn where the session ran ``mnemo sessions`` itself: it is then
  reporting what the queue said, which is not a promise either.

Unbacked is the number #306 wants to see fall. It is a proxy — whether the
text *promises* anything is a question of language (the real case is in
Portuguese), and ``--list`` prints every report's opening so a count can be
read against what was actually said.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from typing import Any, Dict, List, Optional

from mnemo.core.sessions.detector import is_human_turn

#: The footer as printed, one line of output. Anchored so the source line
#: ``print("  queue:  mnemo sessions")`` does not match.
FOOTER = re.compile(r"^\s*queue:  mnemo sessions\s*$", re.MULTILINE)

#: A command that runs dispatch, however mnemo was invoked (``mnemo``,
#: ``python3 -m mnemo``, a frozen binary path ending in ``mnemo``).
DISPATCH_COMMAND = re.compile(r"\bmnemo dispatch\b")

#: The note #306 added; its presence dates a report as after the change.
NOTE = "tells this session when they block or finish"
#: The note's wording once the finish notice carries a report card (#426).
#: Either phrase dates a report as after #306.
NOTE_NOTIFIED = "mnemo posts a notice into this session"

#: The queue, run by the session itself: naming a command it just ran is a
#: report of what it saw, not a promise.
QUEUE_COMMAND = re.compile(r"\bmnemo sessions\b")

#: Tools that can re-invoke a session later, so a promise to report back can
#: actually be kept.
WAKERS = frozenset({"Monitor", "ScheduleWakeup"})


def _records(path: str) -> List[Dict[str, Any]]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    out.append(record)
    except OSError:
        pass
    return out


def _result_text(block: Dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return ""


def _blocks(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    content = (record.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def reports_in(path: str) -> List[Dict[str, Any]]:
    """Every dispatch report in one transcript, in order."""
    commands: Dict[str, str] = {}
    found: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for record in _records(path):
        if is_human_turn(record):
            current = None
            continue
        kind = record.get("type")
        if kind == "user":
            for block in _blocks(record):
                if block.get("type") != "tool_result":
                    continue
                command = commands.get(block.get("tool_use_id", ""), "")
                output = _result_text(block)
                if DISPATCH_COMMAND.search(command) and FOOTER.search(output):
                    current = {
                        "transcript": path,
                        "timestamp": record.get("timestamp", ""),
                        "note_shown": NOTE in output or NOTE_NOTIFIED in output,
                        "text": "",
                        "watcher": False,
                        "ran_queue": False,
                    }
                    found.append(current)
        elif kind == "assistant":
            for block in _blocks(record):
                if block.get("type") == "tool_use":
                    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                    commands[block.get("id", "")] = str(tool_input.get("command", ""))
                    if current is not None and (
                        block.get("name") in WAKERS or tool_input.get("run_in_background")
                    ):
                        current["watcher"] = True
                    if current is not None and QUEUE_COMMAND.search(commands[block.get("id", "")]):
                        current["ran_queue"] = True
                elif block.get("type") == "text" and current is not None:
                    current["text"] += block.get("text", "") + "\n"

    for report in found:
        report["mentions"] = "mnemo sessions" in report["text"]
        report["unbacked"] = (
            report["mentions"] and not report["watcher"] and not report["ran_queue"]
        )
    return found


def measure(projects: str) -> Dict[str, Any]:
    reports: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(projects, "*", "*.jsonl"))):
        reports.extend(reports_in(path))
    reports.sort(key=lambda r: r["timestamp"])

    def bucket(rows: List[Dict[str, Any]]) -> Dict[str, int]:
        return {
            "reports": len(rows),
            "mentions": sum(r["mentions"] for r in rows),
            "unbacked": sum(r["unbacked"] for r in rows),
        }

    return {
        "before_note": bucket([r for r in reports if not r["note_shown"]]),
        "with_note": bucket([r for r in reports if r["note_shown"]]),
        "reports": reports,
    }


def format_report(report: Dict[str, Any], *, listing: bool = False) -> str:
    lines = []
    for label, key in (("before the note", "before_note"), ("with the note", "with_note")):
        b = report[key]
        lines.append(f"{label:16} {b['reports']:3} dispatch reports, "
                     f"{b['mentions']:3} name `mnemo sessions`, {b['unbacked']:3} unbacked")
    if listing:
        lines.append("")
        for r in report["reports"]:
            flag = ("UNBACKED" if r["unbacked"] else "watcher " if r["watcher"]
                    else "ran it  " if r["ran_queue"] else "        ")
            project = os.path.basename(os.path.dirname(r["transcript"]))
            opening = " ".join(r["text"].split())[:110]
            lines.append(f"{r['timestamp'][:16]}  {flag}  {project[-28:]:28}  {opening}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--list", action="store_true", help="print every dispatch report's opening")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = measure(args.projects)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report, listing=args.list), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
