"""``mnemo friction`` — the friction ledger, reported and backfilled.

    mnemo friction                   # read-only report of the ledger
    mnemo friction --backfill        # dry run (one LLM call per session) → .mnemo/friction-backfill-plan.json
    mnemo friction --backfill --fresh   # ...re-briefing instead of reusing the last dry run
    mnemo friction --apply           # write the saved dry run to the ledger (no LLM)

``--since DATE`` and ``--project NAME`` bound any of them; ``--json`` for all.
See :mod:`mnemo.core.friction.backfill`.
"""
from __future__ import annotations

import argparse
import json
import sys

from mnemo.cli.parser import command


@command("friction")
def cmd_friction(args: argparse.Namespace) -> int:
    from mnemo import cli
    from mnemo.core.friction import backfill as BF

    vault = cli._resolve_vault()
    use_json = bool(getattr(args, "json", False))
    since = getattr(args, "since", None)
    project = getattr(args, "project", None)
    do_backfill = bool(getattr(args, "backfill", False))
    do_apply = bool(getattr(args, "apply", False))

    if do_backfill and do_apply:
        print("pick one: --backfill plans, --apply writes the saved plan.")
        return 1
    if getattr(args, "fresh", False) and not do_backfill:
        print("--fresh only applies to --backfill.")
        return 1

    if do_apply:
        return _apply(BF, vault, use_json=use_json)

    if do_backfill:
        return _backfill(BF, vault, since=since, project=project,
                         fresh=bool(getattr(args, "fresh", False)), use_json=use_json)

    try:
        summary = BF.summarize(vault, project=project, since=since)
    except ValueError as exc:
        print(str(exc))
        return 1
    print(json.dumps(summary, indent=2, ensure_ascii=False) if use_json else BF.format_summary(summary))
    return 0


def _backfill(BF, vault, *, since, project, fresh: bool, use_json: bool) -> int:
    path = BF.plan_path(vault)

    def progress(entry) -> None:
        # stderr, so --json stays one parseable document on stdout.
        print(BF.format_session(entry), file=sys.stderr, flush=True)

    try:
        plan = BF.plan(vault, since=since, project=project, fresh=fresh, progress=progress)
    except ValueError as exc:
        print(str(exc))
        return 1
    except KeyboardInterrupt:
        print(f"\ninterrupted — what was planned is saved to {path}; "
              "rerun `mnemo friction --backfill` to continue where it stopped.", file=sys.stderr)
        return 130
    if use_json:
        print(path.read_text(encoding="utf-8"))
    else:
        print(BF.format_plan(plan, sessions=False))
        print(f"(saved to {path}; scratch briefings under {BF.scratch_root(vault) / 'briefings'})")
    return 0


def _apply(BF, vault, *, use_json: bool) -> int:
    path = BF.plan_path(vault)
    if not path.exists():
        print(f"no dry run saved at {path} — run `mnemo friction --backfill` first.")
        return 1
    plan = BF.load_plan(path)
    report = BF.apply(vault, plan)
    if use_json:
        print(json.dumps(report.to_dict(), indent=2))
        return 1 if report.telemetry_off else 0
    if report.telemetry_off:
        print("telemetry is switched off (injection.telemetry.enabled); the ledger follows that "
              "switch, so nothing was written.")
        return 1
    note = "" if plan.complete else " (the plan is from an interrupted sweep; rerun --backfill to finish it)"
    print(f"wrote {len(report.written)} correction(s) from {report.sessions} session(s) to the ledger"
          f"; {report.duplicates} already there" + (f", {report.failed} failed (see .errors.log)"
                                                    if report.failed else "") + note)
    return 1 if report.failed else 0
