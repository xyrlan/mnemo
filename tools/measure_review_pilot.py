"""The readout of #661's A/B twins pilot: does the review arm win?

Reads ``--arms`` pairs from ``<vault>/.mnemo/dispatch-twins.jsonl``
(:mod:`mnemo.core.twins`) and the checkpoints' ledger
``<vault>/.mnemo/review-ledger.jsonl`` (:mod:`mnemo.core.review`), and prints
pairs, decided pairs, review-arm wins, ties, output tokens per arm, and the
findings' accepted/rejected counts. Then the verdict for the bar #661
declared before any pair ran:

    positive  <=>  the review arm wins at least 80% of the decided pairs
                   AND at least 6 pairs are decided.

Positive: the switch (``dispatch.review``) defaults on. Anything else: the
feature is removed, with no second round. Cost is printed next to the
verdict and does not decide it.

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

from mnemo.core import review, twins  # noqa: E402

#: #661's bar, declared before the pilot.
WIN_SHARE = 0.80
MIN_DECIDED = 6


def _tokens(pair: twins.Pair, arm: str) -> Optional[int]:
    for t in pair.twins:
        if t.arm == arm:
            value = (pair.metrics.get(t.tag) or {}).get("tokens")
            return value if isinstance(value, int) else None
    return None


def readout(pairs: Sequence[twins.Pair], ledger: Sequence[Mapping[str, Any]], *,
            exclude_conditions: bool = False) -> Dict[str, Any]:
    """Counts and the verdict over *pairs* (``--arms`` ones only)."""
    rows: List[Dict[str, Any]] = []
    excluded = 0
    for pair in pairs:
        if not pair.arms or len(pair.started) != len(twins.LABELS):
            continue
        if exclude_conditions and any(
                twins.human_input(pair.conditions.get(t.tag)) for t in pair.started):
            excluded += 1
            continue
        reviewed = next((t.tag for t in pair.twins if t.arm == twins.REVIEW_ARM), "")
        if not pair.choice:
            outcome = "unanswered"
        elif pair.choice == twins.TIE:
            outcome = "tie"
        else:
            outcome = "review" if pair.preferred == reviewed else "control"
        mine = [r for r in ledger if r.get("twin_tag") == reviewed and reviewed]
        rows.append({
            "pair": pair.pair, "issue": pair.issue, "outcome": outcome,
            "tokens_review": _tokens(pair, twins.REVIEW_ARM),
            "tokens_control": _tokens(pair, twins.CONTROL_ARM),
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
    result = readout(list(twins.read_pairs(vault).values()), review.read_ledger(vault),
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
