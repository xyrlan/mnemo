#!/usr/bin/env python3
"""Count which auto-memory writes stopped for a permission prompt, by path spelling (#637).

A Claude Code session started with ``CLAUDE_CONFIG_DIR=~/.claude-<slug>``,
where that directory's ``projects`` is a symlink to ``~/.claude/projects``, is
told to keep its memory under ``~/.claude-<slug>/projects/<project>/memory/``.
Claude Code prompts for a write spelled that way and not for the same file
spelled through ``~/.claude``. This reads the transcripts and says how often
each happened::

    python3 tools/measure_memory_write_prompts.py [--projects DIR] [--days N] [--json]

What is counted. Every ``Write``, ``Edit`` or ``MultiEdit`` ``tool_use`` whose
``file_path`` is ``<config dir>/projects/<project>/memory/…``, paired by id
with the ``tool_result`` row that answers it. Three things are read off the
pair:

- the **spelling**, from the path the model asked for: ``default`` when the
  config dir is named ``.claude``, ``account`` when it is ``.claude-<slug>``,
  ``other`` for anything else that has the shape (a ``projects/x/memory``
  directory in some repo). The slug is not printed: it can be an address;
- the **decision**, the result row's ``permissionDecision`` (Claude Code
  records it since 2.1.293): ``decision``, ``source`` and ``reasonType``.
  ``source: user_temporary`` is a prompt a person answered; ``source: config``
  is no prompt, and its ``reasonType`` says what let it through
  (``classifier`` in ``auto`` mode). A result row without the field is
  ``not recorded``, a call with no result row is ``no result``: a session
  killed while it waited, or one still waiting;
- the **wait**, the result row's timestamp minus the call's, in seconds.

What mnemo's PreToolUse hook does to an ``account`` write shows two ways.
``rewritten`` counts the writes whose result names another path than the one
asked for (``toolUseResult.filePath``): a Write that created a file, handed
back under its real path. ``reject`` from ``source: hook`` is an edit the hook
sent to the real path instead; the edit the model then makes there is a
``default`` row. An ``account`` row with ``source: user_temporary`` is a
prompt the hook did not remove.

A transcript a resumed session copied rows into repeats their ids; each id is
counted once. Subagent transcripts (``<session>/subagents/*.jsonl``) are read
too. Percentiles are nearest-rank,
:func:`mnemo.core.reflex.judge_stats.percentile`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mnemo.core.reflex.judge_stats import percentile  # noqa: E402

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

#: The tools that write the file their ``file_path`` names.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})

SPELLINGS = ("account", "default", "other")

# <config dir>/projects/<project>/memory/<file>, on either separator.
_MEMORY_PATH = re.compile(r"^(?P<config>.*?)/projects/[^/]+/memory/.+$")
_RESULT_ID = re.compile(r'"tool_use_id"\s*:\s*"([^"]+)"')


def _posix(path: str) -> str:
    return path.replace("\\", "/")


def spelling(file_path: Any) -> Optional[str]:
    """``account``, ``default`` or ``other`` for a path inside a
    ``projects/<project>/memory/`` directory; ``None`` for any other path."""
    if not isinstance(file_path, str):
        return None
    match = _MEMORY_PATH.match(_posix(file_path))
    if match is None:
        return None
    config = match.group("config").rsplit("/", 1)[-1]
    if config == ".claude":
        return "default"
    if config.startswith(".claude-"):
        return "account"
    return "other"


def _when(stamp: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None


def _blocks(row: Dict[str, Any], kind: str) -> Iterator[Dict[str, Any]]:
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == kind:
                yield block


def transcript_rows(path: str) -> Iterator[Dict[str, Any]]:
    """The rows of one transcript that can hold a memory write or its result.

    A result line is parsed only when it answers a call already seen in this
    file, so the cost is the memory writes and not every tool call on disk.
    """
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    pending = set()  # type: set
    with handle:
        for line in handle:
            is_use = '"tool_use"' in line and "memory" in line
            is_result = bool(pending) and any(
                i in pending for i in _RESULT_ID.findall(line))
            if not (is_use or is_result):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            for block in _blocks(row, "tool_use"):
                if isinstance(block.get("id"), str):
                    pending.add(block["id"])
            yield row


def memory_writes(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One record per memory write in *rows*, in the order they were called:
    ``id``, ``spelling``, ``tool``, ``at``, ``decision`` (``None`` with no result row,
    ``{}`` when the row records none), ``wait_s`` and ``rewritten``."""
    calls = {}  # type: Dict[str, Dict[str, Any]]
    for row in rows:
        for block in _blocks(row, "tool_use"):
            call_id = block.get("id")
            tool_input = block.get("input")
            if (not isinstance(call_id, str) or call_id in calls
                    or block.get("name") not in WRITE_TOOLS
                    or not isinstance(tool_input, dict)):
                continue
            kind = spelling(tool_input.get("file_path"))
            if kind is None:
                continue
            calls[call_id] = {
                "id": call_id, "spelling": kind, "tool": block.get("name"), "at": row.get("timestamp"),
                "file_path": tool_input["file_path"], "decision": None,
                "wait_s": None, "rewritten": False,
            }
        for block in _blocks(row, "tool_result"):
            call = calls.get(block.get("tool_use_id"))
            if call is None or call["decision"] is not None:
                continue
            decision = row.get("permissionDecision")
            call["decision"] = decision if isinstance(decision, dict) else {}
            asked, answered = _when(call["at"]), _when(row.get("timestamp"))
            if asked is not None and answered is not None:
                call["wait_s"] = (answered - asked).total_seconds()
            result = row.get("toolUseResult")
            applied = result.get("filePath") if isinstance(result, dict) else None
            if isinstance(applied, str) and applied:
                call["rewritten"] = _posix(applied) != _posix(call["file_path"])
    return list(calls.values())


def decision_label(decision: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    """``(decision, source, reasonType)`` as the report keys a row by."""
    if decision is None:
        return ("no result", "-", "-")
    if not decision:
        return ("not recorded", "-", "-")
    return (str(decision.get("decision", "-")), str(decision.get("source", "-")),
            str(decision.get("reasonType", "-")))


def _spread(values: List[float]) -> Dict[str, Any]:
    return {"n": len(values), "p50": percentile(values, 0.5),
            "p95": percentile(values, 0.95), "max": max(values) if values else None}


def summarize(writes: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Per spelling: the writes, how many were rewritten, and one row per
    ``(decision, source, reasonType)`` with its count and its wait."""
    by = {name: {} for name in SPELLINGS}  # type: Dict[str, Dict[Tuple[str, str, str], List[Dict[str, Any]]]]
    for write in writes:
        by[write["spelling"]].setdefault(decision_label(write["decision"]), []).append(write)
    report = {}  # type: Dict[str, Any]
    for name in SPELLINGS:
        groups = by[name]
        rows = []
        for key in sorted(groups, key=lambda k: (-len(groups[k]), k)):
            group = groups[key]
            rows.append({
                "decision": key[0], "source": key[1], "reasonType": key[2],
                "writes": len(group),
                "rewritten": sum(1 for w in group if w["rewritten"]),
                "wait_s": _spread([w["wait_s"] for w in group if w["wait_s"] is not None]),
            })
        everything = [w for group in groups.values() for w in group]
        report[name] = {
            "writes": len(everything),
            "rewritten": sum(1 for w in everything if w["rewritten"]),
            "prompted": sum(1 for w in everything
                            if (w["decision"] or {}).get("source") == "user_temporary"),
            "decisions": rows,
        }
    return report


def _cutoff(days: Optional[int]) -> Optional[datetime]:
    if days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def measure(projects_root: str, *, days: Optional[int] = None) -> Dict[str, Any]:
    root = Path(projects_root)
    since = _cutoff(days)
    seen = {}  # type: Dict[str, Dict[str, Any]]
    transcripts = 0
    for path in sorted(root.glob("**/*.jsonl")):
        transcripts += 1
        for write in memory_writes(transcript_rows(str(path))):
            at = _when(write["at"])
            if since is not None and (at is None or at < since):
                continue
            # A resumed session's transcript repeats the rows it copied, ids
            # included; the copy that has its result wins over one cut off
            # before it.
            key = write["id"]
            if key not in seen or seen[key]["decision"] is None:
                seen[key] = write
    writes = list(seen.values())
    stamps = sorted(str(w["at"]) for w in writes if w["at"])
    return {
        "transcripts": transcripts,
        "days": days,
        "writes": len(writes),
        "first": stamps[0] if stamps else None,
        "last": stamps[-1] if stamps else None,
        "spellings": summarize(writes),
    }


def _seconds(value: Any) -> str:
    return "-" if value is None else "%.1f" % value


_NAMES = {
    "account": "account  (~/.claude-<slug>/projects/…/memory/)",
    "default": "default  (~/.claude/projects/…/memory/)",
    "other": "other    (…/projects/…/memory/ elsewhere)",
}


def render(report: Dict[str, Any]) -> str:
    lines = ["memory writes (Write/Edit/MultiEdit): %d in %d transcripts%s, %s to %s" % (
        report["writes"], report["transcripts"],
        "" if report["days"] is None else ", last %d days" % report["days"],
        (report["first"] or "-")[:10], (report["last"] or "-")[:10])]
    for name in SPELLINGS:
        block = report["spellings"][name]
        lines += ["", "%s: %d writes, %d prompted, %d rewritten" % (
            _NAMES[name], block["writes"], block["prompted"], block["rewritten"])]
        if not block["decisions"]:
            continue
        lines.append("  %6s %9s  %-12s %-15s %-11s %9s %9s %9s" % (
            "writes", "rewritten", "decision", "source", "reasonType",
            "wait p50", "p95", "max (s)"))
        for row in block["decisions"]:
            wait = row["wait_s"]
            lines.append("  %6d %9d  %-12s %-15s %-11s %9s %9s %9s" % (
                row["writes"], row["rewritten"], row["decision"], row["source"],
                row["reasonType"], _seconds(wait["p50"]), _seconds(wait["p95"]),
                _seconds(wait["max"])))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--days", type=int, default=None,
                        help="only writes called in the last N days (default: every one)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    projects = os.path.expanduser(args.projects)
    report = measure(projects, days=args.days)
    prov = _provenance.provenance(__file__, argv, blind_spots=[
        _provenance.transcripts_blind_spot(projects),
        "a result row written before Claude Code 2.1.293 records no permissionDecision",
    ])
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=2))
    else:
        print(render(report))
        print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
