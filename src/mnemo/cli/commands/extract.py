"""``mnemo extract`` — LLM-powered extraction into ``shared/_inbox``.

Foreground path prints a per-run summary; ``--background`` (used by the
auto-brain SessionStart hook) suppresses stdout/stderr and routes
errors to ``~/.errors.log`` via :mod:`mnemo.core.errors`.
"""
from __future__ import annotations

import argparse
import sys

from mnemo.cli.parser import command


@command("extract")
def cmd_extract(args: argparse.Namespace) -> int:
    from mnemo.core import config as cfg_mod, extract as extract_mod

    cfg = cfg_mod.load_config()
    if bool(getattr(args, "background", False)):
        return _run_extract_background(cfg, args)

    try:
        summary = extract_mod.run_extraction(cfg, dry_run=bool(args.dry_run), force=bool(args.force))
    except extract_mod.ExtractionIOError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. Partial state flushed; re-run to continue.", file=sys.stderr)
        return 130

    if args.dry_run:
        return 0

    # Summary
    total_tokens = summary.total_input_tokens + summary.total_output_tokens
    if summary.all_calls_subscription:
        cost_line = f"{total_tokens} tokens processed (subscription — no charge)"
    else:
        cost_line = f"${summary.total_cost_usd:.4f} ({total_tokens} tokens)"

    cluster_pages = summary.pages_written - summary.projects_promoted
    print("✓ extraction complete")
    print(f"  written:    {summary.pages_written} pages ({summary.projects_promoted} direct projects + {cluster_pages} cluster pages)")
    print(f"  auto-promoted: {summary.auto_promoted}")
    print(f"  conflicts:  {summary.sibling_proposed + summary.sibling_bounced}")
    print(f"  upgrades:   {summary.upgrade_proposed}")
    print(f"  updates:    {summary.update_proposed}")
    print(f"  skipped:    {summary.unchanged_skipped} unchanged, {summary.dismissed_skipped} dismissed")
    print(f"  calls:      {summary.llm_calls} LLM calls")
    print(f"  wall-time:  {summary.wall_time_s:.1f}s")
    print(f"  cost:       {cost_line}")

    if summary.failed_chunks > 0:
        print(f"  ⚠ failed_chunks: {summary.failed_chunks} (see ~/.errors.log; re-run to retry)", file=sys.stderr)
        return 1
    return 0


def _run_extract_background(cfg: dict, args: argparse.Namespace) -> int:
    import contextlib
    import os
    from mnemo.core import errors as err_mod, extract as extract_mod, paths as paths_mod

    vault_root = paths_mod.vault_root(cfg)
    devnull = open(os.devnull, "w")
    try:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            try:
                extract_mod.run_extraction(
                    cfg,
                    dry_run=bool(args.dry_run),
                    force=bool(args.force),
                    background=True,
                )
            except extract_mod.ExtractionIOError as exc:
                # Lock contention: do NOT write last-auto-run.json; log only
                err_mod.log_error(vault_root, "extract.bg.lock", exc)
                return 2
            except Exception as exc:
                # run_extraction in background mode catches most errors and
                # writes them into last-auto-run.json; this path is for
                # exceptions that escape (e.g., config issues).
                err_mod.log_error(vault_root, "extract.bg.outer", exc)
                return 1
    finally:
        devnull.close()
    return 0
