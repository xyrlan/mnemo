"""``mnemo backfill`` — populate a vault from archived session transcripts.

Two entry shapes:

- ``--install-run``: the capped, current-repo-only sweep that ``session_start``
  spawns once after install. Non-interactive by construction.
- everything else: an explicit sweep the user asks for, which prints an
  estimate and asks before spending.

Per-session failures are recorded and stepped over — one malformed transcript
must never abort a sweep.
"""
from __future__ import annotations

import argparse
import os

from mnemo.cli.parser import command
from mnemo.core import config as cfg_mod
from mnemo.core import errors as err_mod
from mnemo.core import paths
from mnemo.core.backfill import discover, harvest, ledger


def _select(args: argparse.Namespace, cfg: dict) -> list:
    """Which transcripts this invocation should consider, newest first."""
    backfill_cfg = cfg.get("backfill") or {}
    if args.install_run:
        from mnemo.core import agent as agent_mod

        project = agent_mod.resolve_canonical_agent(os.getcwd()).name
        limit = int(backfill_cfg.get("installCap", 20))
        return discover.find_transcripts(project=project, limit=limit)

    project = args.project
    if project is None and not args.all:
        from mnemo.core import agent as agent_mod

        project = agent_mod.resolve_canonical_agent(os.getcwd()).name
    limit = int(args.limit) if args.limit else None
    return discover.find_transcripts(project=project, limit=limit)


@command("backfill")
def cmd_backfill(args: argparse.Namespace) -> int:
    cfg = cfg_mod.load_config()
    backfill_cfg = cfg.get("backfill") or {}
    if not backfill_cfg.get("enabled", True):
        print("backfill: disabled in config (backfill.enabled = false)")
        return 0

    vault_root = paths.vault_root(cfg)
    candidates = _select(args, cfg)
    led = ledger.load(vault_root)
    todo = [t for t in candidates if ledger.should_harvest(led, t.path)]

    if not todo:
        print("backfill: nothing to do — every transcript is already harvested.")
        return 0

    projects = sorted({t.agent for t in todo})
    print(
        f"backfill: {len(todo)} session(s) across {len(projects)} project(s): "
        f"{', '.join(projects)}"
    )
    print(f"          ~{_estimate_input_tokens(todo):,} input tokens, "
          f"{len(todo)} LLM call(s) via your existing claude CLI.")

    if args.dry_run:
        for t in todo:
            print(f"          would harvest {t.agent}/{t.path.name}")
        print("backfill: dry run — nothing written.")
        return 0

    if not args.install_run and not args.yes:
        try:
            reply = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("backfill: cancelled.")
            return 0

    produced = 0
    harvested = 0
    for t in todo:
        try:
            written = harvest.harvest_session(t.path, t.agent, cfg)
        except Exception as exc:  # one bad transcript must not end the sweep
            ledger.mark_failed(led, t.path, str(exc))
            err_mod.log_error(vault_root, "backfill.harvest", exc)
            ledger.save(vault_root, led)
            if _is_rate_limited(exc):
                print("backfill: rate limited — stopping. Rerun to resume.")
                break
            continue
        ledger.mark_done(led, t.path, produced=len(written))
        ledger.save(vault_root, led)
        harvested += 1
        produced += len(written)

    print(f"backfill: harvested {harvested} session(s), wrote {produced} memory file(s).")
    if produced:
        # `extract` is the right next step, but it does not produce live rules
        # from this material: the origin gate stages every backfill-origin page
        # in shared/_inbox/ for a human to confirm, whatever its source count.
        print("          run `mnemo extract` — backfilled pages stage in "
              "shared/_inbox/ for review.")
    return 0


def _estimate_input_tokens(transcripts: list) -> int:
    """Rough input-token estimate for a sweep.

    Transcript bytes are a usable proxy: flattening drops jsonl scaffolding but
    keeps the text, and ~4 bytes per token is the standard approximation. This
    only has to be good enough for a user to judge whether to proceed, so it
    deliberately avoids tokenizing anything.
    """
    total_bytes = 0
    for t in transcripts:
        try:
            total_bytes += t.path.stat().st_size
        except OSError:
            continue
    return total_bytes // 4


def _is_rate_limited(exc: Exception) -> bool:
    from mnemo.core import llm

    return isinstance(exc, llm.LLMSubprocessError) and llm._is_rate_limit(str(exc))
