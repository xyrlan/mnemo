"""How many friction-ledger rows the go-ahead/question screen keeps (#758).

Usage:
    PYTHONPATH=src python3 tools/measure_friction_screen.py [--vault DIR]
        [--ledger FILE ...] [--projects DIR] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
        [--labels DIR] [--list] [--json]

**The ledger replay.** Every row of ``<vault>/.mnemo/friction-ledger.jsonl``
(and each ``--ledger``) written by the session-end capture (``capture`` set;
backfilled rows were not found by this path), recorded between ``--since``
and ``--until`` (inclusive, by the row's ``ts`` date), is put through
:func:`mnemo.core.corrections.screen_reason` — the shipped function, not a
copy. It judges the row's **whole user turn**, as the live
:func:`corrections.screen` does: the turn at ``turn_index`` in the session's
transcript under ``--projects`` (default ``~/.claude/projects``). When the
transcript is gone, or the turn no longer holds the quote, the quote stands
in for the turn and the row is counted under ``quote_only``. A row whose
quote today's :func:`corrections.verify` refuses in its transcript (it lies
only in a compaction summary, say) is counted ``unverified``: the live path no
longer records it either.

**The labelled set** (``--labels DIR``, optional): #519's ``items.json`` and
``labels.json`` (``tools/measure_repeated_corrections.py``), the turns two
blind raters labelled. Reported: of the items both raters call a correction,
how many the screen would drop (the cost), and of the rest how many it drops
(the gain). Read-only; no LLM call.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from mnemo.core import corrections
from mnemo.core.briefing import _load_jsonl_events
from mnemo.core.transcript import user_turns

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]


def transcripts(projects: str) -> Dict[str, Path]:
    """``session id -> transcript path`` for every jsonl under *projects*."""
    out: Dict[str, Path] = {}
    for p in glob.glob(os.path.join(projects, "*", "*.jsonl")):
        out[Path(p).stem] = Path(p)
    return out


def read_rows(paths: Iterable[Path], since: str = "", until: str = "") -> List[Dict[str, Any]]:
    """Capture rows (``capture`` set) of every ledger in *paths*, in order.

    *since* and *until* bound the row's ``ts`` date, inclusive; ``""`` is open.
    """
    rows: List[Dict[str, Any]] = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not (isinstance(row, dict) and row.get("capture") and isinstance(row.get("quote"), str)):
                continue
            day = str(row.get("ts") or "")[:10]
            if (since and day < since) or (until and day > until):
                continue
            rows.append(row)
    return rows


def turn_of(row: Dict[str, Any], turns: Optional[List[str]]) -> Optional[str]:
    """The row's user turn when *turns* still holds its quote there, else None."""
    index = row.get("turn_index")
    if turns is None or not isinstance(index, int) or not 0 <= index < len(turns):
        return None
    turn = turns[index]
    return turn if corrections.quote_matches_turn(row["quote"], turn) else None


def replay(rows: List[Dict[str, Any]], sessions: Dict[str, Path]) -> List[Dict[str, Any]]:
    """One verdict per row: its quote, capture, reason (``""`` = kept), basis.

    The reason is :func:`corrections.screen_reason`'s, or ``"unverified"``.
    """
    cache: Dict[str, Optional[List[str]]] = {}
    out = []
    for row in rows:
        sid = str(row.get("session_id") or "")
        if sid not in cache:
            path = sessions.get(sid)
            cache[sid] = user_turns(_load_jsonl_events(path)) if path else None
        turns = cache[sid]
        turn = turn_of(row, turns)
        if turns is not None and corrections.locate(row["quote"], turns) is None:
            reason = "unverified"
        else:
            reason = corrections.screen_reason(turn if turn is not None else row["quote"])
        out.append({
            "quote": row["quote"],
            "capture": row.get("capture"),
            "reason": reason,
            "basis": "turn" if turn is not None else "quote_only",
        })
    return out


def summary(verdicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_reason: Dict[str, int] = {}
    by_capture: Dict[str, Dict[str, int]] = {}
    for v in verdicts:
        key = v["reason"] or "kept"
        by_reason[key] = by_reason.get(key, 0) + 1
        cap = by_capture.setdefault(str(v["capture"]), {"rows": 0, "kept": 0})
        cap["rows"] += 1
        cap["kept"] += 0 if v["reason"] else 1
    return {
        "rows": len(verdicts),
        "kept": by_reason.get("kept", 0),
        "by_reason": by_reason,
        "by_capture": by_capture,
        "quote_only": sum(1 for v in verdicts if v["basis"] == "quote_only"),
    }


def labelled(items: List[Dict[str, Any]], gold: Dict[str, bool]) -> Dict[str, int]:
    """The screen against #519's raters: what it drops of each kind."""
    c = {"corrections": 0, "corrections_dropped": 0, "others": 0, "others_dropped": 0}
    for it in items:
        if it.get("id") not in gold:
            continue
        kind = "corrections" if gold[it["id"]] else "others"
        c[kind] += 1
        if corrections.screen_reason(str(it.get("turn") or "")):
            c[kind + "_dropped"] += 1
    return c


def _labels(directory: Path) -> Dict[str, Any]:
    spec = importlib.util.spec_from_file_location(
        "measure_correction_check", Path(__file__).resolve().parent / "measure_correction_check.py")
    mcc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcc)
    items = json.loads((directory / "items.json").read_text(encoding="utf-8"))
    labels = json.loads((directory / "labels.json").read_text(encoding="utf-8"))
    return labelled(items, mcc.truth(items, labels, mcc.mrc.RATERS))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--ledger", action="append", default=[], help="another friction-ledger.jsonl")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--since", default="", help="first row date, YYYY-MM-DD")
    ap.add_argument("--until", default="", help="last row date, YYYY-MM-DD")
    ap.add_argument("--labels", default="", help="#519's repeated-corrections dir")
    ap.add_argument("--list", action="store_true", help="print every row's verdict")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    vault = Path(args.vault).expanduser() if args.vault else None
    if vault is None:
        from mnemo.core import config, paths

        vault = paths.vault_root(config.load_config())
    ledgers = [vault / ".mnemo" / "friction-ledger.jsonl"] + [Path(p) for p in args.ledger]
    verdicts = replay(read_rows(ledgers, args.since, args.until), transcripts(args.projects))
    report: Dict[str, Any] = {"ledger": summary(verdicts)}
    if args.labels:
        report["labelled"] = _labels(Path(args.labels).expanduser())
    if args.list:
        report["rows"] = verdicts

    prov = _provenance.provenance(__file__, argv, vault=vault,
                                  blind_spots=[_provenance.transcripts_blind_spot(args.projects)])
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=1, ensure_ascii=False))
        return 0
    s = report["ledger"]
    print("ledger rows: %d  kept: %d  dropped: %d  (judged on the quote alone: %d)" % (
        s["rows"], s["kept"], s["rows"] - s["kept"], s["quote_only"]))
    for reason, n in sorted(s["by_reason"].items()):
        print("  %-9s %d" % (reason, n))
    for cap, c in sorted(s["by_capture"].items()):
        print("  capture=%s: %d of %d kept" % (cap, c["kept"], c["rows"]))
    if "labelled" in report:
        lab = report["labelled"]
        print("labelled (#519): drops %d of %d real corrections, %d of %d others" % (
            lab["corrections_dropped"], lab["corrections"], lab["others_dropped"], lab["others"]))
    for v in report.get("rows", []):
        print("  %-9s %-10s %s" % (v["reason"] or "kept", v["basis"], v["quote"][:100].replace("\n", " ")))
    print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
