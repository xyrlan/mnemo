"""``mnemo recall`` — measure retrieval ranking vs historical access-log queries."""
from __future__ import annotations

import argparse
import sys

from mnemo.cli.parser import command


@command("recall")
def cmd_recall(args: argparse.Namespace) -> int:
    """Measure retrieval ranking against historical queries captured in the access log.

    Reads ``.mnemo/mcp-access-log.jsonl``, pairs each ``list_rules_by_topic`` call with
    the ``read_mnemo_rule`` that consumed it, re-runs the live ranking, and reports
    hit@3/@5/@10 + MRR + p95 latency. Outputs to ``.mnemo/recall-cases.json`` (fixture)
    and ``.mnemo/recall-report.json`` (last run).
    """
    import json as _json
    from datetime import datetime, timezone
    from mnemo import cli  # late binding so monkeypatch.setattr("mnemo.cli._resolve_vault", ...) takes effect
    from mnemo.core.mcp.recall import (
        aggregate, bootstrap_cases, count_log_entries, format_report, run_case,
    )

    vault = cli._resolve_vault()
    mnemo_dir = vault / ".mnemo"
    cases_path = mnemo_dir / "recall-cases.json"
    report_path = mnemo_dir / "recall-report.json"
    log_path = mnemo_dir / "mcp-access-log.jsonl"
    use_json = bool(getattr(args, "json", False))

    orphan_dropped = 0
    if not args.no_bootstrap:
        if not log_path.is_file():
            print(f"error: access log missing: {log_path}", file=sys.stderr)
            return 1
        raw_cases = bootstrap_cases(log_path, pair_window_s=args.window_s)
        cases = bootstrap_cases(
            log_path, pair_window_s=args.window_s, vault_root=vault
        )
        orphan_dropped = len(raw_cases) - len(cases)
        mnemo_dir.mkdir(parents=True, exist_ok=True)
        cases_path.write_text(_json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    else:
        if not cases_path.is_file():
            print(
                f"error: cases file missing: {cases_path} — run `mnemo recall` without --no-bootstrap first",
                file=sys.stderr,
            )
            return 1
        cases = _json.loads(cases_path.read_text(encoding="utf-8"))

    if not cases:
        msg = "no cases generated — access log has no matching list→read pairs yet."
        if use_json:
            print(_json.dumps({"report": None, "results": [], "reason": msg}))
        else:
            print(msg)
        return 0

    results = [run_case(vault, c) for c in cases]
    log_entries = count_log_entries(log_path)
    report = aggregate(results, log_entries=log_entries, orphan_dropped=orphan_dropped)
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "report": report,
        "results": results,
    }
    report_path.write_text(_json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    if use_json:
        print(_json.dumps(payload, indent=2))
    else:
        print(format_report(report))
        print(f"\n(written to {report_path})")
    return 0
