"""How many of the user's corrections the session-end detector captures (#517).

Usage:
    PYTHONPATH=src python3 tools/measure_corrections_capture.py [--since YYYY-MM-DD]
        [--projects DIR] [--vault DIR] [--ledger FILE ...] [--capture-into DIR] [--json]

The population is every Claude Code transcript under ``--projects`` (default
``~/.claude/projects``). A session is **human** when it has at least one
typed turn, its entrypoint is not ``sdk-cli`` (a program driving
``claude -p``), its cwd is not throwaway (``hook_guard.is_throwaway``: temp,
pytest or job-scratch dirs, which no hook writes from), and ``mnemo dispatch``
did not start it (``capture.is_dispatched_child``: dispatch-brief opening or a
``dispatch-parents.jsonl`` link). It is dated by its first timestamp, in UTC,
and bucketed by ISO week.

A session's captured corrections are, in this order:

1. its ledger rows written by the session-end capture (``capture`` set) — in
   ``<vault>/.mnemo/friction-ledger.jsonl`` and every ``--ledger`` file;
2. otherwise the ``## Corrections`` of its briefing under
   ``<vault>/bots/*/briefings/sessions/<sid>.md``, re-checked with today's
   ``corrections.verify`` against the transcript — what the detector kept
   before #517, minus quotes today's rules refuse.

Backfilled ledger rows are not counted: they were found by
``mnemo friction --backfill``, not by the detector this measures.

Per week it prints human sessions, briefed ones, sessions with at least one
captured correction, corrections, and how many of those came from sessions
with no file edit.

Read-only unless ``--capture-into DIR`` is given. That runs the shipped
corrections-only pass (``capture.corrections_only``) over every human session
with no edit and no captured row, writing its rows to ``DIR``'s ledger — never
the real vault's — and then counts with that ledger added. It is how the
"after" number in #517's PR was produced: what the live path would have
captured over the same transcripts. It costs one LLM call per such session
with a typed turn long enough to quote.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from mnemo.core import corrections
from mnemo.core.briefing import _count_file_mutations, _load_jsonl_events, _parse_timestamp
from mnemo.core.friction import capture, ledger
from mnemo.core.hook_guard import is_throwaway
from mnemo.core.log_utils import iter_rotated_rows
from mnemo.core.transcript import user_turns


def first_timestamp(events: List[dict]) -> Optional[datetime]:
    stamps = [_parse_timestamp(ev.get("timestamp")) for ev in events if isinstance(ev, dict)]
    real = [t for t in stamps if t is not None]
    if not real:
        return None
    first = min(real)
    return first if first.tzinfo else first.replace(tzinfo=timezone.utc)


def session_cwd(events: List[dict]) -> str:
    for ev in events:
        if isinstance(ev, dict) and isinstance(ev.get("cwd"), str) and ev["cwd"]:
            return ev["cwd"]
    return ""


def is_human(events: List[dict], session_id: str, parents: Set[str]) -> bool:
    """The population #517's metric counts. See the module docstring."""
    turns = user_turns(events)
    if not turns:
        return False
    if capture.entrypoint(events) == "sdk-cli":
        return False
    if is_throwaway(session_cwd(events)):
        return False
    if corrections.is_dispatch_brief(turns[0]) or session_id[:8] in parents:
        return False
    return True


def briefing_corrections(briefing_text: str, events: List[dict]) -> int:
    """Corrections in a briefing's section that today's ``verify`` keeps."""
    items = corrections.parse_section(briefing_text)
    kept, _ = corrections.verify(items, user_turns(events))
    return len(kept)


def session_row(
    events: List[dict],
    session_id: str,
    *,
    parents: Set[str],
    briefing_text: Optional[str],
    captured_rows: int,
) -> Optional[Dict[str, Any]]:
    """One human session's facts, or None when it is not in the population."""
    if not is_human(events, session_id, parents):
        return None
    when = first_timestamp(events)
    if when is None:
        return None
    when = when.astimezone(timezone.utc)
    year, week, _ = when.isocalendar()
    if captured_rows:
        found, source = captured_rows, "ledger"
    elif briefing_text is not None:
        found, source = briefing_corrections(briefing_text, events), "briefing"
    else:
        found, source = 0, ""
    return {
        "session_id": session_id,
        "week": f"{year}-W{week:02d}",
        "date": when.date().isoformat(),
        "edits": _count_file_mutations(events) > 0,
        "briefed": briefing_text is not None,
        "corrections": found,
        "source": source,
    }


def weekly(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per ISO week: sessions, briefed, with ≥1 correction, corrections, from no-edit."""
    weeks: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        w = weeks.setdefault(r["week"], {
            "week": r["week"], "human_sessions": 0, "briefed": 0,
            "with_correction": 0, "corrections": 0, "no_edit_corrections": 0,
        })
        w["human_sessions"] += 1
        w["briefed"] += int(r["briefed"])
        if r["corrections"]:
            w["with_correction"] += 1
            w["corrections"] += r["corrections"]
            if not r["edits"]:
                w["no_edit_corrections"] += r["corrections"]
    return [weeks[k] for k in sorted(weeks)]


def totals(weeks: List[Dict[str, Any]]) -> Dict[str, Any]:
    keys = ("human_sessions", "briefed", "with_correction", "corrections", "no_edit_corrections")
    out: Dict[str, Any] = {"week": "total"}
    for k in keys:
        out[k] = sum(w[k] for w in weeks)
    return out


def captured_counts(ledger_files: Iterable[Path]) -> Dict[str, int]:
    """``session_id -> rows`` written by the session-end capture, over all files.

    Keyed on ``(session_id, turn_index, quote)`` across files so one row read
    through two paths counts once.
    """
    seen: Set[tuple] = set()
    out: Dict[str, int] = {}
    for path in ledger_files:
        for raw in iter_rotated_rows(Path(path)):
            rec = ledger._from_row(raw)
            if rec is None or not rec.capture:
                continue
            key = (rec.session_id, rec.turn_index, corrections.normalize(rec.quote))
            if key in seen:
                continue
            seen.add(key)
            out[rec.session_id] = out.get(rec.session_id, 0) + 1
    return out


def _share(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.0f}%" if whole else "—"


def render(weeks: List[Dict[str, Any]]) -> str:
    head = f"{'week':<9} {'human':>6} {'briefed':>8} {'≥1 corr':>8} {'corr':>5} {'no-edit':>8} {'share':>6}"
    lines = [head]
    for w in weeks + [totals(weeks)]:
        lines.append(
            f"{w['week']:<9} {w['human_sessions']:>6} {w['briefed']:>8} "
            f"{w['with_correction']:>8} {w['corrections']:>5} "
            f"{w['no_edit_corrections']:>8} {_share(w['no_edit_corrections'], w['corrections']):>6}"
        )
    return "\n".join(lines)


def _briefing_index(vault: Path) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    for p in glob.glob(str(vault / "bots" / "*" / "briefings" / "sessions" / "*.md")):
        out[Path(p).stem] = Path(p)
    return out


def _parents(vault: Path) -> Set[str]:
    from mnemo.core.sessions import parents

    return set(parents.read(vault))


def _project_of(path: Path, events: List[dict]) -> str:
    """The agent name the hook would brief under, resolved from the cwd."""
    try:
        from mnemo.core import agent

        return agent.resolve_canonical_agent(session_cwd(events)).name
    except Exception:
        return path.parent.name


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default="", help="first session date, YYYY-MM-DD")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--vault", default="")
    ap.add_argument("--ledger", action="append", default=[], help="another friction-ledger.jsonl to count")
    ap.add_argument("--capture-into", default="", help="run corrections-only into DIR's ledger")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from mnemo.core import config, paths

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    ledgers = [ledger.ledger_path(vault)] + [Path(p).expanduser() for p in args.ledger]
    if args.capture_into:
        ledgers.append(ledger.ledger_path(Path(args.capture_into).expanduser()))
    parents = _parents(vault)
    briefings = _briefing_index(vault)
    captured = captured_counts(ledgers)

    rows: List[Dict[str, Any]] = []
    for path in sorted(Path(args.projects).glob("*/*.jsonl")):
        sid = path.stem
        events = _load_jsonl_events(path)
        brief = briefings.get(sid)
        text = brief.read_text(encoding="utf-8", errors="replace") if brief else None
        row = session_row(events, sid, parents=parents, briefing_text=text,
                          captured_rows=captured.get(sid, 0))
        if row is None or (args.since and row["date"] < args.since):
            continue
        if args.capture_into and not row["edits"] and not captured.get(sid):
            scratch_cfg = dict(cfg, vaultRoot=str(Path(args.capture_into).expanduser()))
            try:
                found = capture.corrections_only(path, _project_of(path, events), scratch_cfg,
                                                 events=events)
            except Exception as exc:  # one failed call must not end the sweep
                print(f"  {sid[:8]}: {type(exc).__name__}: {exc}", file=sys.stderr)
                found = []
            if found:
                row.update(corrections=len(found), source="ledger")
            elif row["source"] == "briefing":
                row.update(corrections=0, source="")
        rows.append(row)

    weeks = weekly(rows)
    if args.json:
        print(json.dumps({"weeks": weeks, "total": totals(weeks), "sessions": rows}, indent=1))
    else:
        print(render(weeks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
