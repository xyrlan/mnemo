"""How Grok sessions on this machine end their turns, and what their files say (grok-host w2).

Usage:
    PYTHONPATH=src python3 tools/measure_grok_stops.py [--home DIR ...] [--json]

The counts behind :mod:`mnemo.core.sessions.grok_sessions` and the Grok half
of :mod:`mnemo.core.sessions.stalls`, over every session directory of every
Grok home (``paths.grok_homes()`` unless ``--home`` names some):

- ``events``: ``events.jsonl`` records by ``type``, and ``turn_ended`` by
  ``outcome`` — the phase the session reader decides from.
- ``stop_reasons``: ``updates.jsonl``'s ``turn_completed`` by
  ``stop_reason`` — where a rate limit is told apart from another error.
- ``rate_limits``: of the turns that stopped on ``rate_limit``, how many had
  an ``exhausted`` retry naming a rolling window, by its hours — what dates
  the stall.
- ``error_turns``: ``turn_ended`` ``error`` outcomes, and how many of those
  sessions' last ``turn_completed`` was a rate limit.
- ``context``: ``signals.json``'s ``contextTokensUsed`` against the last
  turn's input in ``usage.json`` when that turn made exactly one model call
  (the only case where the turn's input *is* the context): how many agree
  within 1%, and every pair.

Reads only. Session ids print as their short id and paths not at all.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]


def _jsonl(path: Path) -> List[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def session_dirs(home: Path) -> List[Path]:
    root = home / "sessions"
    try:
        return sorted(d for parent in root.iterdir() if parent.is_dir()
                      for d in parent.iterdir() if d.is_dir())
    except OSError:
        return []


def measure(homes: Sequence[Path]) -> Dict[str, Any]:
    from mnemo.core.grok import paths
    from mnemo.core.sessions import stalls

    events: Counter = Counter()
    outcomes: Counter = Counter()
    stops: Counter = Counter()
    windows: Counter = Counter()
    rate_limits = 0
    error_turns = 0
    error_sessions_rate_limited = 0
    error_sessions = 0
    pairs: List[Dict[str, Any]] = []
    sessions = 0

    for home in homes:
        for directory in session_dirs(home):
            sessions += 1
            session_errors = 0
            for record in _jsonl(directory / "events.jsonl"):
                events[str(record.get("type"))] += 1
                if record.get("type") == "turn_ended":
                    outcomes[str(record.get("outcome"))] += 1
                    if record.get("outcome") == "error":
                        session_errors += 1
            error_turns += session_errors

            updates = _jsonl(directory / stalls.GROK_UPDATES)
            exhausted: Optional[str] = None
            for record in updates:
                update = stalls._grok_update(record)
                kind = update.get("sessionUpdate")
                if kind == "retry_state" and update.get("type") == "exhausted":
                    exhausted = str(update.get("reason") or "")
                elif kind == "turn_completed":
                    stops[str(update.get("stop_reason"))] += 1
                    if update.get("stop_reason") == "rate_limit":
                        rate_limits += 1
                        match = stalls._ROLLING_WINDOW.search(exhausted or "")
                        windows[f"{match.group(1)}h" if match else "none"] += 1
                    exhausted = None
            if session_errors:
                error_sessions += 1
                if stalls.grok_stop_reason(directory) == "rate_limit":
                    error_sessions_rate_limited += 1

            used = _json(directory / "signals.json").get("contextTokensUsed")
            turns = _json(directory / "usage.json").get("turns")
            last = turns[-1] if isinstance(turns, list) and turns and isinstance(turns[-1], dict) else {}
            if isinstance(used, int) and last.get("modelCalls") == 1:
                pairs.append({"session": paths.short_id(directory.name),
                              "context_tokens_used": used,
                              "single_call_input": last.get("inputTokens")})

    agree = sum(1 for p in pairs if isinstance(p["single_call_input"], int)
                and abs(p["context_tokens_used"] - p["single_call_input"])
                <= 0.01 * max(1, p["single_call_input"]))
    return {
        "homes": len(homes),
        "sessions": sessions,
        "events": dict(events.most_common()),
        "turn_outcomes": dict(outcomes.most_common()),
        "stop_reasons": dict(stops.most_common()),
        "rate_limits": {"total": rate_limits, "window": dict(windows.most_common())},
        "error_turns": {"turns": error_turns, "sessions": error_sessions,
                        "sessions_last_stop_rate_limit": error_sessions_rate_limited},
        "context": {"compared": len(pairs), "within_1pct": agree, "pairs": pairs},
    }


def render(report: Dict[str, Any]) -> str:
    lines = [f"{report['sessions']} sessions in {report['homes']} home(s)",
             "events: " + ", ".join(f"{k} {v}" for k, v in report["events"].items()),
             "turn_ended outcomes: " + ", ".join(f"{k} {v}" for k, v in report["turn_outcomes"].items()),
             "turn_completed stop_reason: " + ", ".join(
                 f"{k} {v}" for k, v in report["stop_reasons"].items())]
    limits = report["rate_limits"]
    lines.append(f"rate limits: {limits['total']} (window named: " + ", ".join(
        f"{k} {v}" for k, v in limits["window"].items()) + ")")
    errors = report["error_turns"]
    lines.append(f"error turns: {errors['turns']} in {errors['sessions']} session(s), "
                 f"{errors['sessions_last_stop_rate_limit']} of those sessions last stopped on a rate limit")
    context = report["context"]
    lines.append(f"contextTokensUsed vs a one-call turn's input: {context['within_1pct']} of "
                 f"{context['compared']} within 1%")
    for pair in context["pairs"]:
        lines.append(f"  {pair['session']}  {pair['context_tokens_used']:>8}  "
                     f"{pair['single_call_input']!s:>8}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", action="append", default=None, help="a Grok home (repeatable)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from mnemo.core.grok import paths

    homes = [Path(h).expanduser() for h in args.home] if args.home else paths.grok_homes()
    report = measure(homes)
    prov = _provenance.provenance(__file__, argv)
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=1))
    else:
        print(render(report))
        print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
