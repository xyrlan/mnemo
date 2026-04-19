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
    """
    import contextlib
    import os
    from mnemo.core import briefing as briefing_mod, config as cfg_mod, errors as err_mod, paths

    cfg = cfg_mod.load_config()
    vault_root = paths.vault_root(cfg)
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
