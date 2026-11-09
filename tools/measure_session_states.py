"""Which ``(state, tempo)`` pairs Claude Code's job records hold, and what the
ones blocked on ``state`` alone were doing (#737).

Usage:
    PYTHONPATH=src python3 tools/measure_session_states.py [--jobs DIR] [--json]

``mnemo.core.sessions.jobs`` decides "a human is needed" from ``tempo``
alone, and its docstring states both enumerations as measured. This is the
measurement, so the next look at them is one command, not a hand count:

- **Every pair**, over every account's ``jobs/*/state.json`` (the dirs
  ``mnemo.core.sessions.accounts.roots()`` names), an id found under two dirs
  counted once — the reader the queue itself uses. ``--jobs DIR`` reads one
  dir instead, with its parent as the config dir whose roster says ``live``.
- **Every record whose ``state`` is ``blocked`` and whose ``tempo`` is not**,
  by id, with ``live`` and one verdict, read off its transcript
  (``linkScanPath``):

  - ``answered`` — its ``detail`` is the text of the last human turn at or
    before its ``updatedAt``, whitespace collapsed, whole or cut short. That is
    the shape Claude Code writes when a prompt reaches a session: it sets
    ``tempo`` to ``active`` and ``detail`` to the prompt and leaves ``state``
    as it was (``answer-keeps-blocked-state`` in ``mnemo.core.claude_cli``).
    The seconds from that turn to the write are reported with it.
  - ``no transcript`` — no ``linkScanPath``, or a file that cannot be read.
  - ``unexplained`` — anything else. One of these is a reason to re-read the
    writer before trusting the docstring.

"Human turn" is ``mnemo.core.sessions.detector.is_human_turn``, imported so
this count and the unblock detector cannot disagree about what one is.

Blind spots: a record holds only the last pair written to it, so a pair a
session passed through is never seen; records removed by ``claude rm`` or by
retention are not counted at all. Nothing is printed from ``detail`` or
``needs``: they quote the maintainer and the session.

Read-only, no LLM call. ``accounts.roots()`` makes the one write any mnemo
process makes: the current account's dir, put on file in the vault.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

from mnemo.core.sessions import detector

#: How a value the record does not have prints in the pair table.
MISSING = "—"

#: The verdicts, in the order the report lists them.
VERDICTS = ("answered", "no transcript", "unexplained")

BLIND_SPOTS = (
    "a record holds only its last (state, tempo); pairs a session passed through are not seen",
    "records removed by `claude rm` or retention are not counted",
)


def _when(value: Any) -> Optional[datetime]:
    """An ISO timestamp as Claude Code writes it (``...Z``), or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def last_human_turn(path: Optional[str], until: Optional[datetime]
                    ) -> Tuple[bool, Optional[str], Optional[datetime]]:
    """``(readable, text, at)`` of the last human turn at or before *until*.

    ``readable`` is ``False`` when there is no transcript to read; ``text`` is
    ``None`` when it was read and holds no such turn. A turn with no readable
    timestamp cannot be placed before the write and is skipped.
    """
    if not path:
        return False, None, None
    text: Optional[str] = None
    at: Optional[datetime] = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(record, dict) or not detector.is_human_turn(record):
                    continue
                stamp = _when(record.get("timestamp"))
                if stamp is None or (until is not None and stamp > until):
                    continue
                if at is None or stamp >= at:
                    text, at = detector.turn_text(record), stamp
    except OSError:
        return False, None, None
    return True, text, at


def detail_is(detail: Optional[str], turn: Optional[str]) -> bool:
    """Whether *detail* is *turn* as Claude Code stores it: collapsed, maybe cut."""
    if not detail or not turn:
        return False
    stored = _collapse(detail)
    for mark in ("…", "..."):
        if stored.endswith(mark):
            stored = stored[: -len(mark)].rstrip()
            break
    return bool(stored) and _collapse(turn).startswith(stored)


def explain(session: Any) -> Dict[str, Any]:
    """One ``state=blocked`` record whose ``tempo`` is not, and its verdict."""
    written = _when(session.updated_at)
    readable, turn, at = last_human_turn(session.link_scan_path, written)
    gap: Optional[float] = None
    if not readable:
        verdict = "no transcript"
    elif detail_is(session.detail, turn):
        verdict = "answered"
        if written is not None and at is not None:
            gap = round((written - at).total_seconds(), 2)
    else:
        verdict = "unexplained"
    return {"short_id": session.short_id, "tempo": session.tempo, "live": session.live,
            "verdict": verdict, "seconds_after_turn": gap}


def measure(sessions: Iterable[Any]) -> Dict[str, Any]:
    """The pair counts and the ``state``-only blocked rows of *sessions*."""
    rows = list(sessions)
    pairs = Counter((s.state, s.tempo) for s in rows)
    ordered = sorted(pairs.items(),
                     key=lambda kv: (-kv[1], kv[0][0] or "", kv[0][1] or ""))
    odd = [explain(s) for s in sorted(rows, key=lambda s: s.short_id)
           if s.state == "blocked" and s.tempo != "blocked"]
    verdicts = {v: sum(1 for row in odd if row["verdict"] == v) for v in VERDICTS}
    return {
        "records": len(rows),
        "pairs": [{"state": st, "tempo": te, "count": n} for (st, te), n in ordered],
        "state_blocked_tempo_not": odd,
        "verdicts": verdicts,
    }


def render(report: Dict[str, Any]) -> str:
    lines = [f"(state, tempo) over {report['records']} job records"]
    for row in report["pairs"]:
        lines.append(f"  {row['state'] or MISSING:10s} {row['tempo'] or MISSING:10s} {row['count']:5d}")
    odd = report["state_blocked_tempo_not"]
    lines.append(f"state=blocked with any other tempo: {len(odd)}")
    if odd:
        lines.append("  " + ", ".join(f"{v} {n}" for v, n in report["verdicts"].items()))
    for row in odd:
        after = ("" if row["seconds_after_turn"] is None
                 else f", written {row['seconds_after_turn']:g}s after that turn")
        lines.append(f"  {row['short_id']}  tempo={row['tempo'] or MISSING}  "
                     f"live={row['live']}  {row['verdict']}{after}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jobs", default=None,
                    help="one jobs dir (default: every account's, as `mnemo sessions` reads them)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from mnemo.core.sessions import jobs

    if args.jobs:
        base = Path(args.jobs).expanduser()
        sessions = jobs.read_sessions(base, claude_home=base.parent, grok=False)
    else:
        sessions = jobs.read_sessions(grok=False)

    report = measure(sessions)
    prov = _provenance.provenance(__file__, argv, blind_spots=BLIND_SPOTS)
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=1, ensure_ascii=False))
    else:
        print(render(report))
        print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
