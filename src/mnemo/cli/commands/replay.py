"""``mnemo replay`` — your own transcripts against your own vault.

The user-facing measurement. Every other harness (``recall``,
``recall-sessions``) reports ranks to the maintainer; this one replays every
prompt the user typed through the hook's own decision and says how often a
rule from an *earlier* session would have come back. See
:mod:`mnemo.core.reflex.replay` for what the buckets mean and why "without
the vault" is a complement rather than a second run.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("replay")
def cmd_replay(args: argparse.Namespace) -> int:
    """Replay the transcripts on disk through the reflex decision and report."""
    import json as _json

    from mnemo import cli  # late binding, as every other command does
    from mnemo.core import config as cfg_mod
    from mnemo.core.reflex import replay as rp
    from mnemo.core.reflex.index import build_index
    from mnemo.core.reflex.project_config import load_project_thresholds

    vault = cli._resolve_vault()
    reflex_cfg = cfg_mod.load_config().get("reflex") or {}
    use_json = bool(getattr(args, "json", False))
    only_project = getattr(args, "project", None)

    prompts = rp.collect_prompts(vault)
    if only_project:
        prompts = [p for p in prompts if p.project == only_project]
    if not prompts:
        where = f"for project {only_project!r}" if only_project else "under ~/.claude/projects"
        print(f"replay: no Claude Code transcripts with typed prompts {where} — nothing to replay.")
        return 2

    # Built from the vault as it is, not read from `.mnemo/reflex-index.json`:
    # the on-disk index is whatever the last SessionStart left, and the promise
    # here is that the same vault gives the same number.
    index = build_index(vault)
    if not index.get("doc_count"):
        print("replay: the vault has no rules yet — nothing to replay against.")
        return 2
    facts = rp.rule_facts(vault)

    result = rp.run(
        prompts, index, facts,
        reflex_cfg=reflex_cfg,
        overrides_for=lambda project: load_project_thresholds(vault, project),
    )
    report = rp.aggregate(
        result,
        vault_rules=int(index["doc_count"]),
        correction_backed_rules=sum(1 for f in facts.values() if f.correction_backed),
    )
    if only_project:
        report["project"] = only_project

    report_path = vault / ".mnemo" / "replay-report.json"
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except OSError:
        report_path = None

    if use_json:
        print(_json.dumps(report, indent=2))
        return 0
    print(rp.format_report(report))
    if report_path is not None:
        print(f"\n(written to {report_path})")
    return 0
