"""``mnemo recall-sessions`` — the delta-detector recall harness.

Sibling to ``mnemo recall``, and deliberately not a replacement for it. See
:mod:`mnemo.core.mcp.recall_sessions` for why its absolute numbers are not
comparable to the telemetry harness's, and why it exists anyway.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("recall-sessions")
def cmd_recall_sessions(args: argparse.Namespace) -> int:
    """Score every extraction session's opening prompt against the reflex index."""
    import json as _json

    from mnemo import cli  # late binding, as every other command does
    from mnemo.core.mcp import recall_sessions as rs
    from mnemo.core.reflex.index import load_index

    vault = cli._resolve_vault()
    cases = rs.bootstrap_cases(vault)
    index = load_index(vault)
    if index is None:
        print("recall-sessions: no reflex index — nothing to score against.")
        return 2

    results = [rs.run_case(vault, c, index=index) for c in cases]
    report = rs.aggregate(results)

    if bool(getattr(args, "json", False)):
        print(_json.dumps({"report": report, "results": results}, indent=2))
        return 0

    print(f"cases              : {report['cases']} sessions, "
          f"{report['expected_rules']} expected rule(s)")
    print(f"any@3 / @5 / @10   : {report['any_at_3']} / {report['any_at_5']} / "
          f"{report['any_at_10']}")
    print(f"any rate @3/@5/@10 : {100*report['any_rate_at_3']:.2f}% / "
          f"{100*report['any_rate_at_5']:.2f}% / {100*report['any_rate_at_10']:.2f}%")
    print(f"recall @3/@5/@10   : {100*report['recall_at_3']:.2f}% / "
          f"{100*report['recall_at_5']:.2f}% / {100*report['recall_at_10']:.2f}%")
    print(f"MRR                : {report['mrr']}")
    # Said every run, not in a doc nobody opens next to the number that matters.
    print("note: absolute values are not comparable to `mnemo recall` — different "
          "task (no topic filter) and mild leakage. Use for deltas.")
    return 0
