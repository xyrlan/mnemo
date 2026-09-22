"""Does an agent read the rules the rerank stage marked? (#416)

Usage:
    PYTHONPATH=src python3 tools/measure_rerank_reads.py                  # the access log, local
    PYTHONPATH=src python3 tools/measure_rerank_reads.py --days 14        # the last 14 days of it
    PYTHONPATH=src python3 tools/measure_rerank_reads.py --transcripts    # session transcripts, local
    PYTHONPATH=src python3 tools/measure_rerank_reads.py --json

Nothing here leaves the machine: it reads the access log or the session
transcripts and prints counts.

Since #404 ``recall.rerank`` writes ``relevant: true|false`` on every rule it
judged and moves the marked ones to the top. What the stage exists for is the
agent's next move — does it read what was marked, or ignore it — and
``docs/configuration.md`` lists that as unmeasured. This counts it, per judged
list, as (rule, list) pairs in three buckets: marked, judged but not marked,
and shown but never judged (past ``maxRules``, empty body, skipped by the
judge); for each, how many were read. The issue's three numbers are
marked-then-read, unmarked-then-read and marked-never-read.

Two sources, one pairing rule:

- **the access log** (the default). Since #416 every ``list_rules_by_topic``
  and ``read_mnemo_rule`` row carries the ``session_id`` it ran in, and a
  judged row's ``rerank`` object carries ``scores`` (every slug judged, with
  its signal) and ``relevant_slugs`` (the ones marked). A judged row written
  before #416 has neither and is counted as unauditable, never guessed at.
- **``--transcripts``**: the tool results themselves, whose items carry
  ``relevant`` — the same marks, so calls the log cannot audit still can be.
  One transcript file is one session. Subagents write transcripts of their
  own, which this does not read; the log counts their reads under the parent,
  whose MCP server they share.

A ``read_mnemo_rule`` is credited to the latest earlier list *in the same
session* that showed that slug — ``mnemo recall``'s pairing with the session
in place of its time window. Every list counts as a place the slug could have
come from, queried or not, so a read after an unqueried list is not credited
to an earlier judged one. A read that no earlier list showed is off-list: the
slug came from somewhere else (a briefing, the reflex hook, a rule body).

What it cannot say. The stage puts the marked rules first, so a marked rule is
read both because it is marked and because it is at the top; the two are not
separable here. The baseline rows — queried lists with no marks at all
(stage off, or fell back), split by rank — are the position-only reference,
not a control. And every count is small until the stage has run for a while:
the Wilson interval printed with each rate says how small.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The transcript parsing — what a result block returned, and its marks — is
#: the filter tool's, so the two tools cannot read one result two ways.
mrf = _sibling("measure_rerank_filter")

LIST = "list_rules_by_topic"
READ = "read_mnemo_rule"

#: The baseline's rank split: the top of a list is where marked rules land.
TOP = 3


def _tool(name: str) -> str:
    """``mcp__mnemo__list_rules_by_topic`` and ``list_rules_by_topic`` alike."""
    return name.rsplit("__", 1)[-1]


# --- the two sources, as one stream of events -------------------------------


def events_from_log(rows: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """List and read events from access-log rows, oldest first, and what was skipped.

    ``skipped`` counts judged lists only — the rows this report is about:
    ``before_416`` judged without ``scores`` (logged before the slugs were),
    ``no_session`` judged without a ``session_id`` (a server Claude Code did
    not spawn). Any row without a session is left out, list or read: there is
    no session to pair it within.
    """
    events: List[Dict[str, Any]] = []
    skipped = {"before_416": 0, "no_session": 0}
    for row in rows:
        tool = row.get("tool")
        if tool not in (LIST, READ):
            continue
        args = row.get("args") if isinstance(row.get("args"), dict) else {}
        rerank = row.get("rerank") if isinstance(row.get("rerank"), dict) else {}
        judged_ok = tool == LIST and rerank.get("status") == "ok"
        if judged_ok and not isinstance(rerank.get("scores"), list):
            skipped["before_416"] += 1
            continue
        session = row.get("session_id")
        if not isinstance(session, str) or not session:
            if judged_ok:
                skipped["no_session"] += 1
            continue
        ts = str(row.get("timestamp") or "")
        if tool == READ:
            if args.get("slug"):
                events.append({"kind": "read", "session": session, "ts": ts,
                               "slug": str(args["slug"])})
            continue
        judged = [pair[0] for pair in (rerank.get("scores") or [])
                  if isinstance(pair, list) and pair and isinstance(pair[0], str)] if judged_ok else []
        marked = [slug for slug in (rerank.get("relevant_slugs") or []) if slug in judged]
        events.append({"kind": "list", "session": session, "ts": ts,
                       "topic": str(args.get("topic") or ""), "queried": bool(args.get("query")),
                       "shown": [str(s) for s in (row.get("hit_slugs") or [])],
                       "judged": judged, "marked": marked})
    return events, skipped


def events_from_transcript(records: Iterable[Dict[str, Any]], session: str) -> List[Dict[str, Any]]:
    """List and read events from one transcript, in the order the agent saw them.

    A list is an event once its result is back — paired by ``tool_use_id``,
    like ``measure_rerank_filter.calls_from_records`` — so a read issued in the
    same turn as the list cannot be credited to it. Its marks are the
    ``relevant`` flags in the result (:func:`measure_rerank_filter.result_marks`).
    """
    events: List[Dict[str, Any]] = []
    pending: Dict[Any, Dict[str, Any]] = {}
    for record in records:
        content = (record.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = _tool(str(block.get("name") or ""))
                args = block.get("input") if isinstance(block.get("input"), dict) else {}
                ts = str(record.get("timestamp") or "")
                if name == LIST:
                    pending[block.get("id")] = {
                        "kind": "list", "session": session, "ts": ts,
                        "topic": str(args.get("topic") or ""), "queried": bool(args.get("query"))}
                elif name == READ and args.get("slug"):
                    events.append({"kind": "read", "session": session, "ts": ts,
                                   "slug": str(args["slug"])})
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in pending:
                event = pending.pop(block["tool_use_id"])
                shown = mrf.result_slugs(block.get("content"))
                if not shown:
                    continue
                marks = mrf.result_marks(block.get("content"))
                event.update(shown=shown, judged=[s for s in shown if s in marks],
                             marked=[s for s in shown if marks.get(s)])
                events.append(event)
    return events


def since(events: Iterable[Dict[str, Any]], cutoff: str) -> List[Dict[str, Any]]:
    """The events at or after ``cutoff``, an ISO-8601 UTC timestamp; all of
    them when it is empty.

    A string comparison: both sources write UTC ISO timestamps, which sort
    lexicographically. Under a cutoff an event with no timestamp falls outside.
    """
    if not cutoff:
        return list(events)
    return [event for event in events if event["ts"] and event["ts"] >= cutoff]


# --- the pairing ------------------------------------------------------------


def pair(events: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Every list with the slugs read from it, and how many reads no list showed.

    A read goes to the latest earlier list in its session that showed the
    slug, once per list however often it was read.
    """
    lists: Dict[str, List[Dict[str, Any]]] = {}
    calls: List[Dict[str, Any]] = []
    off_list = 0
    for event in events:
        if event["kind"] == "list":
            call = dict(event, reads=[])
            lists.setdefault(event["session"], []).append(call)
            calls.append(call)
            continue
        for call in reversed(lists.get(event["session"], [])):
            if event["slug"] in call["shown"]:
                if event["slug"] not in call["reads"]:
                    call["reads"].append(event["slug"])
                break
        else:
            off_list += 1
    return calls, off_list


# --- the report -------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.96) -> Optional[Tuple[float, float]]:
    """The 95% Wilson interval of ``k`` in ``n``; ``None`` when ``n`` is 0."""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _bucket(shown: int, read: int) -> Dict[str, Any]:
    interval = wilson(read, shown)
    return {"shown": shown, "read": read,
            "rate": (read / shown) if shown else None,
            "interval": list(interval) if interval else None}


def report(calls: Sequence[Dict[str, Any]], off_list: int,
           skipped: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Read rates for marked, unmarked and unjudged rules, and the baseline."""
    judged = [c for c in calls if c["judged"]]
    baseline = [c for c in calls if c["queried"] and not c["judged"]]
    totals = {"marked": [0, 0], "unmarked": [0, 0], "unjudged": [0, 0],
              "top": [0, 0], "rest": [0, 0]}
    per_call = []
    for call in judged:
        read = set(call["reads"])
        marked = set(call["marked"])
        unmarked = set(call["judged"]) - marked
        unjudged = set(call["shown"]) - set(call["judged"])
        for name, group in (("marked", marked), ("unmarked", unmarked), ("unjudged", unjudged)):
            totals[name][0] += len(group)
            totals[name][1] += len(group & read)
        per_call.append({"session": call["session"], "ts": call["ts"], "topic": call["topic"],
                         "shown": len(call["shown"]), "judged": len(call["judged"]),
                         "marked": len(marked), "marked_read": len(marked & read),
                         "unmarked_read": len(unmarked & read),
                         "unjudged_read": len(unjudged & read)})
    for call in baseline:
        read = set(call["reads"])
        for rank, slug in enumerate(call["shown"], start=1):
            side = totals["top"] if rank <= TOP else totals["rest"]
            side[0] += 1
            side[1] += slug in read
    marked_shown, marked_read = totals["marked"]
    return {
        "judged_calls": len(judged),
        "sessions": len({c["session"] for c in judged}),
        "nothing_marked": sum(1 for c in judged if not c["marked"]),
        "reads_where_nothing_marked": sum(len(c["reads"]) for c in judged if not c["marked"]),
        "marked_then_read": marked_read,
        "unmarked_then_read": totals["unmarked"][1],
        "marked_never_read": marked_shown - marked_read,
        "buckets": {name: _bucket(*totals[name]) for name in ("marked", "unmarked", "unjudged")},
        "baseline_calls": len(baseline),
        "baseline": {"top_%d" % TOP: _bucket(*totals["top"]), "rest": _bucket(*totals["rest"])},
        "off_list_reads": off_list,
        "skipped": dict(skipped or {}),
        "calls": per_call,
    }


def _rate(bucket: Dict[str, Any]) -> str:
    if not bucket["shown"]:
        return "%5d %5d     -" % (0, 0)
    low, high = bucket["interval"]
    return "%5d %5d  %4.0f%%  [%.0f%%, %.0f%%]" % (
        bucket["shown"], bucket["read"], 100 * bucket["rate"], 100 * low, 100 * high)


def format_report(data: Dict[str, Any], source: str) -> str:
    lines = ["rerank marks against reads — %s" % source, ""]
    skipped = data["skipped"]
    if skipped:
        lines.append("judged lists left out: %d logged before #416 (no slugs), "
                     "%d without a session_id" % (skipped.get("before_416", 0),
                                                  skipped.get("no_session", 0)))
    lines.append("judged lists: %d in %d session(s); %d with nothing marked, "
                 "%d read(s) from those" % (data["judged_calls"], data["sessions"],
                                            data["nothing_marked"],
                                            data["reads_where_nothing_marked"]))
    lines.append("marked-then-read %d   unmarked-then-read %d   marked-never-read %d"
                 % (data["marked_then_read"], data["unmarked_then_read"],
                    data["marked_never_read"]))
    lines.append("")
    lines.append("%-26s %5s %5s  %5s  %s" % ("rules", "shown", "read", "rate", "95% Wilson"))
    for name, label in (("marked", "marked relevant"), ("unmarked", "judged, not marked"),
                        ("unjudged", "shown, never judged")):
        lines.append("%-26s %s" % (label, _rate(data["buckets"][name])))
    lines.append("")
    lines.append("baseline — %d queried list(s) with no marks (position only, not a control):"
                 % data["baseline_calls"])
    for name, label in (("top_%d" % TOP, "ranks 1-%d" % TOP), ("rest", "rank %d+" % (TOP + 1))):
        lines.append("%-26s %s" % (label, _rate(data["baseline"][name])))
    lines.append("")
    lines.append("reads no earlier list in the session showed: %d" % data["off_list_reads"])
    return "\n".join(lines)


# --- the vault side: everything below reads files, nothing above does -------


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_events(vault: Path, cutoff: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """The window is applied to the rows, so what was skipped is counted inside it too."""
    from mnemo.core.log_utils import iter_rotated_rows

    rows = iter_rotated_rows(vault / ".mnemo" / "mcp-access-log.jsonl")
    return events_from_log(row for row in rows if str(row.get("timestamp") or "") >= cutoff)


def _transcript_events(projects: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for path in mrf._transcripts(projects):
        events.extend(events_from_transcript(mrf._records(path), session=path.stem))
    return events


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--transcripts", action="store_true",
                        help="read the marks from session transcripts instead of the access log")
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--days", type=int, default=0,
                        help="only the last N days (default: everything on disk)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    cutoff = _cutoff(args.days) if args.days > 0 else ""
    if args.transcripts:
        events, skipped = since(_transcript_events(args.projects), cutoff), {}
        source = "session transcripts under %s" % args.projects
    else:
        from mnemo import cli

        vault = cli._resolve_vault()
        events, skipped = _log_events(vault, cutoff)
        source = str(vault / ".mnemo" / "mcp-access-log.jsonl")
    if cutoff:
        source += ", last %d days" % args.days
    calls, off_list = pair(events)
    data = report(calls, off_list, skipped)
    if args.json:
        print(json.dumps(dict(data, source=source), indent=2))
    else:
        print(format_report(data, source))
    return 0


if __name__ == "__main__":
    sys.exit(main())
