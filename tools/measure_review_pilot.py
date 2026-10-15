"""The readout of #661's A/B twins pilot: does the review arm win?

Reads ``--arms`` pairs from ``<vault>/.mnemo/dispatch-twins.jsonl``
(:mod:`mnemo.core.twins`) and the checkpoints' ledger
``<vault>/.mnemo/review-ledger.jsonl``, and prints
pairs, decided pairs, review-arm wins, ties, output tokens per arm, and the
findings' accepted/rejected counts. Then the verdict for the bar #661
declared before any pair ran:

    positive  <=>  the review arm wins at least 80% of the decided pairs
                   AND at least 6 pairs are decided.

Positive: the switch (``dispatch.review``) defaults on. Anything else: the
feature is removed, with no second round. Cost is printed next to the
verdict and does not decide it.

The pilot read negative and #682 removed the feature: no new pair records an
arm and nothing writes the ledger any more. This tool still reads the ones
the pilot left, on its own: which twin had which arm comes from each pair's
``arms``/``arm`` fields in the raw log, which :func:`mnemo.core.twins.read_pairs`
no longer parses, and the ledger is read here too. Over the maintainer's log
it prints 10 answered, 8 decided, review wins 5 of 8; the 4 of 8 #661 reported
is in ``design/findings/2026-10-15-review-pilot-661.md``, with why they differ.

A pair counts once it has a blind answer (``mnemo twins prefer``); a tie is
answered but not decided. ``--exclude-conditions`` leaves out pairs where a
person reached a twin mid-run (#453's ``conditions``).

Usage::

    python3 tools/measure_review_pilot.py [--vault PATH] [--exclude-conditions] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

from mnemo.core import twins  # noqa: E402

#: #661's bar, declared before the pilot.
WIN_SHARE = 0.80
MIN_DECIDED = 6

#: The two arms of an ``--arms`` pair, as the log spells them.
REVIEW_ARM = "review"
CONTROL_ARM = "control"

#: The checkpoints' ledger, one row per finding a reviewed twin decided.
LEDGER_NAME = "review-ledger.jsonl"

#: ``{pair id: {twin tag: arm}}`` — what :func:`read_arms` returns.
Arms = Dict[str, Dict[str, str]]


def read_arms(vault_root: Path) -> Arms:
    """Each ``--arms`` pair's ``{tag: arm}``, from its ``pair`` event. Never raises."""
    try:
        text = twins.log_path(vault_root).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    out: Arms = {}
    for raw in text.splitlines():
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if not (isinstance(ev, dict) and ev.get("event") == "pair" and ev.get("arms")):
            continue
        entries = ev.get("twins")
        if not isinstance(entries, list):
            continue
        out[str(ev.get("pair"))] = {str(t.get("tag")): str(t.get("arm") or "")
                                    for t in entries if isinstance(t, dict)}
    return out


def read_ledger(vault_root: Path) -> List[Dict[str, Any]]:
    """Every ledger row, oldest first. Never raises."""
    try:
        text = (Path(vault_root) / ".mnemo" / LEDGER_NAME).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    out = []
    for raw in text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _tokens(pair: twins.Pair, tag: str) -> Optional[int]:
    value = (pair.metrics.get(tag) or {}).get("tokens") if tag else None
    return value if isinstance(value, int) else None


def readout(pairs: Sequence[twins.Pair], arms: Arms, ledger: Sequence[Mapping[str, Any]], *,
            exclude_conditions: bool = False) -> Dict[str, Any]:
    """Counts and the verdict over *pairs* (``--arms`` ones only, per *arms*)."""
    rows: List[Dict[str, Any]] = []
    excluded = 0
    for pair in pairs:
        arm_of = arms.get(pair.pair)
        if not arm_of or len(pair.started) != len(twins.LABELS):
            continue
        if exclude_conditions and any(
                twins.human_input(pair.conditions.get(t.tag)) for t in pair.started):
            excluded += 1
            continue
        reviewed = next((t.tag for t in pair.twins if arm_of.get(t.tag) == REVIEW_ARM), "")
        control = next((t.tag for t in pair.twins if arm_of.get(t.tag) == CONTROL_ARM), "")
        if not pair.choice:
            outcome = "unanswered"
        elif pair.choice == twins.TIE:
            outcome = "tie"
        else:
            outcome = "review" if pair.preferred == reviewed else "control"
        mine = [r for r in ledger if r.get("twin_tag") == reviewed and reviewed]
        rows.append({
            "pair": pair.pair, "issue": pair.issue, "outcome": outcome,
            "tokens_review": _tokens(pair, reviewed),
            "tokens_control": _tokens(pair, control),
            "accepted": sum(r.get("outcome") == "accepted" for r in mine),
            "rejected": sum(r.get("outcome") == "rejected" for r in mine),
        })
    wins = sum(r["outcome"] == "review" for r in rows)
    losses = sum(r["outcome"] == "control" for r in rows)
    decided = wins + losses
    share = wins / decided if decided else None
    positive = decided >= MIN_DECIDED and share is not None and share >= WIN_SHARE

    def total(key: str) -> int:
        return sum(r[key] or 0 for r in rows)

    return {
        "pairs": len(rows), "answered": sum(r["outcome"] != "unanswered" for r in rows),
        "decided": decided, "review_wins": wins, "control_wins": losses,
        "ties": sum(r["outcome"] == "tie" for r in rows),
        "win_share": share, "excluded_for_conditions": excluded,
        "tokens_review": total("tokens_review"), "tokens_control": total("tokens_control"),
        "findings_accepted": total("accepted"), "findings_rejected": total("rejected"),
        "verdict": "positive" if positive else "negative",
        "then": ("dispatch.review defaults on" if positive
                 else "remove the feature; no second round"),
        "rows": rows,
    }


def report_lines(r: Mapping[str, Any]) -> List[str]:
    lines = [f"{row['pair']}  #{row['issue']}  {row['outcome']:<10}  tokens "
             f"review {row['tokens_review']} / control {row['tokens_control']}  "
             f"findings {row['accepted']} accepted, {row['rejected']} rejected"
             for row in r["rows"]]
    share = "—" if r["win_share"] is None else f"{r['win_share']:.0%}"
    lines += [
        "",
        f"pairs {r['pairs']}, answered {r['answered']}, decided {r['decided']}, "
        f"ties {r['ties']}" + (f", excluded for conditions {r['excluded_for_conditions']}"
                              if r["excluded_for_conditions"] else ""),
        f"review arm wins {r['review_wins']} of {r['decided']} decided ({share}); "
        f"bar: >= {WIN_SHARE:.0%} of >= {MIN_DECIDED} decided",
        f"findings: {r['findings_accepted']} accepted, {r['findings_rejected']} rejected",
        f"cost (output tokens): review arm {r['tokens_review']}, control arm "
        f"{r['tokens_control']} — printed, does not decide",
        f"VERDICT: {r['verdict']} — {r['then']}",
    ]
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--vault", type=Path, default=None)
    ap.add_argument("--exclude-conditions", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    vault = args.vault or twins.default_vault()
    result = readout(list(twins.read_pairs(vault).values()), read_arms(vault),
                     read_ledger(vault),
                     exclude_conditions=args.exclude_conditions)
    prov = _provenance.provenance(
        __file__, argv, vault=vault,
        blind_spots=["tokens are each twin's output tokens at the first `show`; "
                     "a pair never shown has none"])
    if args.json:
        print(json.dumps(_provenance.stamp(result, prov), indent=1))
    else:
        print("\n".join(report_lines(result)))
        print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
