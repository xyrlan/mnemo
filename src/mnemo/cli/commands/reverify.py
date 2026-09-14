"""``mnemo reverify`` — re-brief the sessions behind label-only verified
rules and let today's evidence gate decide (#257).

    mnemo reverify              # dry run (LLM, one call per session) → .mnemo/reverify-plan.json
    mnemo reverify --fresh      # ...ignoring the scratch briefings of the last dry run
    mnemo reverify --apply      # execute the saved dry run (no LLM)
    mnemo reverify --undo ID    # restore every touched file, byte for byte

See :mod:`mnemo.core.reverify` for the outcomes and why nothing is demoted
without a re-brief.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("reverify")
def cmd_reverify(args: argparse.Namespace) -> int:
    import json as _json

    from mnemo import cli
    from mnemo.core import reverify as RV
    from mnemo.core.config import load_config

    vault = cli._resolve_vault()
    plan_path = vault / ".mnemo" / RV.PLAN_NAME
    scratch = vault / ".mnemo" / RV.SCRATCH_DIR
    use_json = bool(getattr(args, "json", False))

    if getattr(args, "undo", None):
        restored = RV.undo(vault, args.undo)
        if not restored:
            print(f"no reverify run {args.undo} found (nothing restored)")
            return 1
        print(f"restored {restored} file(s) from reclassify-{args.undo}")
        return 0

    if getattr(args, "apply", False):
        if not plan_path.exists():
            print(f"no dry run saved at {plan_path} — run `mnemo reverify` first.")
            return 1
        report = RV.load_report(plan_path)
        try:
            applied = RV.apply(vault, report, scratch=scratch)
        except RuntimeError as exc:
            print(str(exc))
            return 1
        if use_json:
            print(_json.dumps({"run_id": report.run_id, "kept": applied.kept, "demoted": applied.demoted,
                               "skipped": applied.skipped}, indent=2))
        else:
            print(RV.format_report(report, applied=applied))
        return 0

    cfg = load_config()
    briefer = RV.default_briefer(cfg, fresh=bool(getattr(args, "fresh", False)))
    report = RV.run(vault, scratch=scratch, briefer=briefer)
    RV.save_report(report, plan_path)
    if use_json:
        print(plan_path.read_text(encoding="utf-8"))
    else:
        print(RV.format_report(report))
        print(f"\n(saved to {plan_path}; regenerated briefings under {scratch / 'briefings'})")
    return 0
