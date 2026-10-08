"""``mnemo session-start-deferred``: the session-start work nobody waits for.

SessionStart spawns it detached on every start (#610): the memory mirror and
the reflex index rebuild, which feed no block the hook injects. See
:func:`mnemo.hooks.session_start.run_deferred`.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("session-start-deferred")
def cmd_session_start_deferred(args: argparse.Namespace) -> int:  # noqa: ARG001
    from mnemo.core import config as cfg_mod, errors as err_mod, paths
    from mnemo.hooks import session_start

    cfg = cfg_mod.load_config()
    vault_root = paths.vault_root(cfg)
    try:
        session_start.run_deferred(cfg, vault_root)
    except Exception as exc:  # noqa: BLE001 — detached; nobody reads a traceback
        err_mod.log_error(vault_root, "session_start.deferred", exc)
        return 1
    return 0
