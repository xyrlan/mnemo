"""``mnemo briefing`` — hidden CLI hook invoked by session_end's detached spawn."""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.parser import command


@command("briefing")
def cmd_briefing(args: argparse.Namespace) -> int:
    """Hidden CLI entry point: `mnemo briefing <jsonl_path> <agent>`.

    Invoked by session_end's detached spawn. Fire-and-forget: errors are
    logged to ~/.errors.log under the vault but never propagated.

    `mnemo briefing --prune [--dry-run]` runs the retention pass (#116) and
    prints its report — the one mode of this command a human runs.
    """
    import contextlib
    import os
    import sys
    from mnemo.core import briefing as briefing_mod, config as cfg_mod, errors as err_mod, paths

    cfg = cfg_mod.load_config()
    vault_root = paths.vault_root(cfg)
    if getattr(args, "prune", False):
        rep = briefing_mod.prune(vault_root, cfg, dry_run=args.dry_run)
        verb = "would delete" if args.dry_run else "deleted"
        print(f"Briefings: {rep.scanned} across {rep.agents} agent(s) ({rep.bytes / 1024:.0f} KB)")
        print(
            f"  protected by rule sources: {rep.protected_by_sources}; "
            f"kept (newest per agent): {rep.kept_min}; within retention: {rep.kept_recent}"
        )
        print(f"  {verb} {len(rep.deleted)}")
        for p in rep.deleted[:20]:
            print(f"    • {p.relative_to(vault_root).as_posix()}")
        if len(rep.deleted) > 20:
            print(f"    … {len(rep.deleted) - 20} more")
        return 0
    if not args.jsonl_path or not args.agent:
        print(
            "usage: mnemo briefing <jsonl_path> <agent> | mnemo briefing --prune [--dry-run]",
            file=sys.stderr,
        )
        return 2
    devnull = open(os.devnull, "w")
    try:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            try:
                briefing_mod.generate_session_briefing(
                    Path(args.jsonl_path), args.agent, cfg,
                )
            except Exception as exc:
                err_mod.log_error(vault_root, "briefing.cli", exc)
                return 1
    finally:
        devnull.close()
    return 0
