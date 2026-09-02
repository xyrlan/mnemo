"""Replay the keep verdicts of a saved reclassify plan through the #119 gate.

Usage:
    PYTHONPATH=src python3 tools/calibrate_keep_bar.py ~/mnemo/.mnemo/reclassify-plan.json [--threshold N]

No LLM calls, no writes. Prints how many keeps survive ``quote_is_specific``
and lists the demoted ones with their quotes, so the threshold can be judged
against real user turns rather than fixtures.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mnemo.core import corrections


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan", type=Path)
    ap.add_argument("--threshold", type=int, default=None,
                    help=f"override MIN_CONTENT_TOKENS (default {corrections.MIN_CONTENT_TOKENS})")
    ns = ap.parse_args(argv)
    if ns.threshold is not None:
        corrections.MIN_CONTENT_TOKENS = ns.threshold
    plan = json.loads(ns.plan.read_text(encoding="utf-8"))
    keeps = [v for v in plan.get("verdicts", []) if v.get("verdict") == "keep"]
    ok = [v for v in keeps if corrections.quote_is_specific(v.get("quote") or "")]
    bad = [v for v in keeps if v not in ok]
    print(f"threshold {corrections.MIN_CONTENT_TOKENS}: keep verdicts {len(keeps)}  "
          f"survive {len(ok)}  demoted as generic {len(bad)}")
    for v in bad:
        quote = (v.get("quote") or "").replace("\n", " ")[:70]
        print(f"  - {str(v.get('slug', ''))[:60]:60} | {quote!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
