"""``mnemo child-notices`` — hidden, spawned detached by SessionStart and
``mnemo dispatch``.

Tells the parent of every dispatched child that stopped without its
``SessionEnd`` notice (#502), on a slow tick while any child is still
running. One per vault — a second exits on the lock — and gone once nothing
is left to watch. See :mod:`mnemo.core.sessions.child_notices`.

It runs ``git`` and ``gh`` through the reporters it starts, and never
``claude``, so it cannot fire a hook of its own (#329).
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("child-notices")
def cmd_child_notices(args: argparse.Namespace) -> int:  # noqa: ARG001
    import contextlib
    import os

    from mnemo.core import config as cfg_mod, errors as err_mod, paths
    from mnemo.core.sessions import child_notices

    cfg = cfg_mod.load_config()
    vault_root = paths.vault_root(cfg)
    devnull = open(os.devnull, "w", encoding="utf-8")
    try:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            try:
                child_notices.watch(cfg, vault_root=vault_root)
                return 0
            except Exception as exc:  # noqa: BLE001 — detached; nobody reads a traceback
                err_mod.log_error(vault_root, "child_notices.cli", exc)
                return 1
    finally:
        devnull.close()
