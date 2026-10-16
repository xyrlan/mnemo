"""How often mnemo's text reached a Grok session's model, per channel (spec decision 6).

Usage:
    PYTHONPATH=src python3 tools/measure_grok_reach.py
    PYTHONPATH=src python3 tools/measure_grok_reach.py --home ~/.grok --since 2026-10-16 --json

**Read only.** It reads each session's ``chat_history.jsonl`` under every
Grok home (``mnemo.core.grok.paths.grok_homes``, or the ``--home`` given):
the conversation the model saw, not what mnemo meant to send. Grok drops
SessionStart and UserPromptSubmit context, so mnemo sends through two other
channels, and each block it sends opens with a marker this tool looks for:

* **briefing at turn 1.** The rules file ``.grok/rules/mnemo-briefing.md``
  (:data:`briefing_file.MARKER`), which Grok puts in the first user record
  before the first prompt, or, for a session that started without the file,
  the same block on its first tool call (``MARKERS["briefing"]``). Counted
  per session with at least one prompt.
* **per-prompt context.** The reflex block and held child notices, which
  reach the model on the first PreToolUse after the prompt
  (``MARKERS["prompt"]``, ``MARKERS["held"]``). Counted per turn: a prompt
  and everything up to the next one. A turn with no tool call cannot carry
  it, so the turns without one are counted too.

A turn is a ``user`` record with ``prompt_index`` (measured on grok 1.0.60:
the prompt's record carries it; the rules record and the hook reminders,
``synthetic_reason: system_reminder``, do not). How many turns mnemo had
something to say on is not in the history; ``reflex-log.jsonl`` holds that,
by session id.

First run, 2026-10-16 13:30 UTC, on the machine that built it: 15
histories, 11 sessions with a prompt, 16 turns, 7 of them (44%) with no
tool call. Briefing at turn 1: 0/11; per-prompt context: 0/16. Every one of
those sessions is a probe the Grok round's pieces ran before this code was
installed anywhere (the one run through it hit the free account's usage
limit before the model's first turn), so the zeros say nothing yet about
the channels. The 44% is about turns, not mnemo: on these probes a turn
with no tool call is common, and such a turn never carries per-prompt
context. Real numbers need real Grok sessions with mnemo's hooks.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from mnemo.core.grok import briefing_file
from mnemo.core.grok import hooks as grok_hooks
from mnemo.core.grok import paths as grok_paths

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

FILE_MARKER = briefing_file.MARKER
TOOL_CALL_BRIEFING = grok_hooks.MARKERS[grok_hooks.BRIEFING]
PROMPT_MARKERS = (grok_hooks.MARKERS[grok_hooks.PROMPT], grok_hooks.MARKERS[grok_hooks.HELD])


def read_records(path: Path) -> List[Dict[str, Any]]:
    """The JSON records of one ``chat_history.jsonl``; unreadable lines skipped."""
    out: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def text_of(record: Dict[str, Any]) -> str:
    """Every text block of a record, joined."""
    content = record.get("content")
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
    return "\n".join(parts)


def _is_prompt(record: Dict[str, Any]) -> bool:
    return record.get("type") == "user" and "prompt_index" in record


def _injected(record: Dict[str, Any]) -> bool:
    return record.get("type") == "user" and bool(record.get("synthetic_reason"))


def session_report(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """What reached the model in one session's records."""
    prompts = [i for i, r in enumerate(records) if _is_prompt(r)]
    if not prompts:
        return {"turns": 0}
    head = records[:prompts[0]]
    via_file = any(FILE_MARKER in text_of(r) for r in head)
    turns = []
    for n, start in enumerate(prompts):
        end = prompts[n + 1] if n + 1 < len(prompts) else len(records)
        body = records[start + 1:end]
        injected = [text_of(r) for r in body if _injected(r)]
        turns.append({
            "tool_call": any(r.get("type") == "assistant" and r.get("tool_calls") for r in body),
            "context": any(m in t for t in injected for m in PROMPT_MARKERS),
            "briefing": any(TOOL_CALL_BRIEFING in t for t in injected),
        })
    return {
        "turns": len(turns),
        "briefing_via_file": via_file,
        "briefing_via_tool_call": turns[0]["briefing"],
        "briefing_later": (not via_file and not turns[0]["briefing"]
                           and any(t["briefing"] for t in turns[1:])),
        "turns_with_tool_call": sum(1 for t in turns if t["tool_call"]),
        "turns_with_context": sum(1 for t in turns if t["context"]),
    }


def _started(session_dir: Path) -> Optional[datetime]:
    """When a session started: its UUIDv7 id's timestamp, else the history's mtime."""
    hexid = session_dir.name.replace("-", "")
    try:
        if len(hexid) == 32 and hexid[12] == "7":
            return datetime.fromtimestamp(int(hexid[:12], 16) / 1000.0)
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(grok_paths.chat_history_path(session_dir).stat().st_mtime)
    except OSError:
        return None


def histories(homes: Sequence[Path], since: Optional[datetime] = None) -> Iterator[Path]:
    """Every ``chat_history.jsonl`` under ``homes`` of a session started on or after ``since``."""
    for home in homes:
        root = grok_paths.sessions_root(Path(home))
        try:
            cwd_dirs = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for cwd_dir in cwd_dirs:
            try:
                session_dirs = sorted(p for p in cwd_dir.iterdir() if p.is_dir())
            except OSError:
                continue
            for session_dir in session_dirs:
                history = grok_paths.chat_history_path(session_dir)
                if not history.is_file():
                    continue
                if since is not None:
                    started = _started(session_dir)
                    if started is None or started < since:
                        continue
                yield history


def measure(homes: Sequence[Path], since: Optional[datetime] = None) -> Dict[str, Any]:
    reports = [session_report(read_records(h)) for h in histories(homes, since)]
    seen = [r for r in reports if r["turns"]]
    turns = sum(r["turns"] for r in seen)
    with_tool = sum(r["turns_with_tool_call"] for r in seen)
    return {
        "homes": [str(h) for h in homes],
        "since": since.date().isoformat() if since is not None else None,
        "histories": len(reports),
        "sessions": len(seen),
        "briefing": {
            "via_file": sum(1 for r in seen if r["briefing_via_file"]),
            "via_first_tool_call": sum(1 for r in seen if r["briefing_via_tool_call"]),
            "at_turn_1": sum(1 for r in seen
                             if r["briefing_via_file"] or r["briefing_via_tool_call"]),
            "after_turn_1": sum(1 for r in seen if r["briefing_later"]),
        },
        "prompt_context": {
            "turns": turns,
            "turns_with_tool_call": with_tool,
            "turns_without_tool_call": turns - with_tool,
            "turns_reached": sum(r["turns_with_context"] for r in seen),
        },
    }


def _pct(n: int, d: int) -> str:
    return f"{n}/{d}" + (f" ({100.0 * n / d:.0f}%)" if d else "")


def render(report: Dict[str, Any]) -> str:
    b, p, s = report["briefing"], report["prompt_context"], report["sessions"]
    return "\n".join([
        f"homes {', '.join(report['homes']) or '(none)'}  since {report['since'] or 'all'}",
        f"histories {report['histories']}  sessions with a prompt {s}",
        f"briefing at turn 1: {_pct(b['at_turn_1'], s)}  "
        f"(rules file {b['via_file']}, first tool call {b['via_first_tool_call']}; "
        f"later than turn 1 {b['after_turn_1']})",
        f"per-prompt context: reached {_pct(p['turns_reached'], p['turns'])} turns; "
        f"turns with no tool call (cannot carry it) {_pct(p['turns_without_tool_call'], p['turns'])}",
    ])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", action="append", type=Path, default=None,
                    help="a Grok home to read (repeatable; default: every home on this machine)")
    ap.add_argument("--since", default=None, help="only sessions started on or after DATE (YYYY-MM-DD)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    homes = [h.expanduser() for h in args.home] if args.home else grok_paths.grok_homes()
    since = datetime.strptime(args.since, "%Y-%m-%d") if args.since else None
    report = measure(homes, since)
    blind = ["a session Grok has pruned from disk is not counted",
             "turns where mnemo had nothing to send are in the denominator"]
    prov = _provenance.provenance(__file__, argv, blind_spots=blind)
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=2))
    else:
        print(render(report))
        print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
