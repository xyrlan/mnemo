"""`mnemo reclassify` — grade legacy feedback rules under the evidence rules.

Three modes, deliberately separate so the LLM pass is paid for once and the
destructive step is reviewable:

    mnemo reclassify              # plan (LLM) → .mnemo/reclassify-plan.json
    mnemo reclassify --apply      # execute the saved plan (no LLM)
    mnemo reclassify --undo ID    # restore every touched file, byte for byte
"""
from __future__ import annotations

import argparse
import math

from mnemo.cli.parser import command

BATCH_SIZE = 10


def _fmt(v) -> str:
    if v.verdict == "merge":
        return f"  merge    {v.slug} → {v.target}"
    if v.verdict == "keep":
        quote = (v.quote or "").replace("\n", " ")
        if len(quote) > 70:
            quote = quote[:70] + "…"
        return f'  keep     {v.slug} · "{quote}"'
    reason = f" · {v.reason}" if v.reason else ""
    return f"  {v.verdict:<8} {v.slug}{reason}"


@command("reclassify")
def cmd_reclassify(args: argparse.Namespace) -> int:
    from mnemo import cli
    from mnemo.core import reclassify as R
    from mnemo.core.config import load_config

    vault = cli._resolve_vault()

    if getattr(args, "undo", None):
        run_id = args.undo
        restored = R.undo(vault, run_id)
        if not restored:
            print(f"no reclassify run {run_id} found (nothing restored)")
            return 1
        print(f"restored {restored} file(s) from reclassify-{run_id}")
        return 0

    if getattr(args, "apply", False):
        plan = R.load_plan(vault)
        if plan is None:
            print("no saved plan — run `mnemo reclassify` first")
            return 1
        report = R.apply(vault, plan)
        for note in report.notes:
            print(f"  note: {note}")
        print(
            f"kept {report.kept} · demoted {report.demoted} · "
            f"merged {report.merged} · archived {report.archived}"
        )
        print(f"undo with: mnemo reclassify --undo {plan.run_id}")
        return 0

    cfg = load_config()
    model = cfg.get("model") or "claude-haiku-4-5"
    timeout = int(cfg.get("llm_timeout") or 60)

    rules = R.collect_rules(vault)
    limit = getattr(args, "limit", None)
    if limit is not None:
        rules = rules[:limit]
    if not rules:
        print("no feedback rules to grade")
        return 0

    calls = math.ceil(len(rules) / BATCH_SIZE)
    print(f"{len(rules)} feedback rule(s) · about {calls} {model} call(s)")
    if not getattr(args, "yes", False):
        if input("continue? [y/N] ").strip().lower() not in ("y", "yes"):
            print("aborted")
            return 0

    plan = R.plan(
        vault, model=model, timeout=timeout, batch_size=BATCH_SIZE, limit=limit,
    )
    R.save_plan(vault, plan)

    counts = {v: 0 for v in R.VERDICTS}
    for verdict in plan.verdicts:
        print(_fmt(verdict))
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1

    print(
        f"\nkeep {counts['keep']} · demote {counts['demote']} · "
        f"merge {counts['merge']} · archive {counts['archive']} "
        f"({plan.llm_calls} LLM call(s))"
    )
    print("plan saved — review, then `mnemo reclassify --apply`")
    return 0
