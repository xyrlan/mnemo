"""``mnemo pr-follow`` — hidden, spawned detached by a child's ``SessionEnd``.

Follows every dispatched child's PR on the ledger and wakes the child when
its PR goes red, gets a review comment or conflicts with its base (#436).
One per vault — a second exits on the lock — and gone once nothing is left
to follow. See :mod:`mnemo.core.sessions.pr_follow`.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("pr-follow")
def cmd_pr_follow(args: argparse.Namespace) -> int:  # noqa: ARG001
    import contextlib
    import os

    from mnemo.core import config as cfg_mod, errors as err_mod, paths
    from mnemo.core.sessions import pr_follow

    cfg = cfg_mod.load_config()
    vault_root = paths.vault_root(cfg)
    devnull = open(os.devnull, "w", encoding="utf-8")
    try:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            try:
                pr_follow.watch(cfg, vault_root=vault_root)
                return 0
            except Exception as exc:  # noqa: BLE001 — detached; nobody reads a traceback
                err_mod.log_error(vault_root, "pr_follow.cli", exc)
                return 1
    finally:
        devnull.close()
