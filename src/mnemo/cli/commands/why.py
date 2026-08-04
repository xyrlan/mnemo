"""``mnemo why`` — what reflex decided on the last few prompts, and why.

Reflex is the one part of mnemo that acts without being asked and without
announcing itself: it scores every rule scoped to the project on each prompt,
and stays silent unless a candidate clears three gates. Silence is the correct
default and also completely opaque — a user who never sees an injection cannot
tell a vault with nothing relevant in it from a threshold set slightly too
high.

This reads the receipts the ``UserPromptSubmit`` hook records and prints them.
The explaining lives in :mod:`mnemo.core.reflex.receipts`; this module is the
argument surface and the project scoping.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


def _current_project() -> str:
    """Canonical agent name for the cwd — the repo the user is standing in."""
    import os

    from mnemo.core import agent as agent_mod

    return agent_mod.resolve_canonical_agent(os.getcwd()).name


@command("why")
def cmd_why(args: argparse.Namespace) -> int:
    """Explain the most recent reflex decisions for this repo."""
    import json as _json

    from mnemo import cli  # late binding, as every other command does
    from mnemo.core.reflex import receipts

    vault = cli._resolve_vault()
    project = None if bool(getattr(args, "all_projects", False)) else _current_project()
    decisions = receipts.read_decisions(
        vault, project=project, limit=int(getattr(args, "limit", 10) or 10)
    )

    if bool(getattr(args, "json", False)):
        print(_json.dumps(decisions, indent=2))
        return 0

    print(receipts.format_human(decisions, project=project))
    return 0
