"""``mnemo tree-sweep``: remove the worktrees of children whose PR merged.

SessionStart spawns it detached, at most every half hour per repo (#503).
Typed by hand in a repo, it sweeps that repo now and prints one line per
dispatch tree. See :mod:`mnemo.core.sessions.tree_sweep`.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("tree-sweep")
def cmd_tree_sweep(args: argparse.Namespace) -> int:  # noqa: ARG001
    import os
    import sys

    from mnemo.core import agent as agent_mod
    from mnemo.core import config as cfg_mod, errors as err_mod, paths
    from mnemo.core.sessions import tree_sweep

    cfg = cfg_mod.load_config()
    vault_root = paths.vault_root(cfg)
    here = os.getcwd()
    info = agent_mod.resolve_canonical_agent(here)
    if not info.has_git or not info.repo_root:
        print("tree-sweep: not inside a git repository", file=sys.stderr)
        return 1
    try:
        report = tree_sweep.sweep(
            cfg, vault_root=vault_root, repo_root=info.repo_root, skip=here,
        )
    except Exception as exc:  # noqa: BLE001 — usually detached; nobody reads a traceback
        err_mod.log_error(vault_root, "tree_sweep.cli", exc)
        return 1
    if report.locked:
        print("tree-sweep: another sweep is running")
        return 0
    for v in report.verdicts:
        detail = tree_sweep.render(v) if v.outcome in tree_sweep.TOLD else (
            f"{v.tree.name}: {v.outcome}")
        print(detail)
    return 0
