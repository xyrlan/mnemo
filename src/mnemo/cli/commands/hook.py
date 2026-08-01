"""``mnemo hook <event>`` — dispatch a hook by name.

Hooks are normally wired as ``python -m mnemo.hooks.<event>``, which needs an
importable package on the other end. A frozen/standalone build has no module
path to name, so it wires ``mnemo hook <event>`` instead and lands here.

Both forms stay supported: the ``-m`` entry points remain, and
:func:`mnemo.install.settings.is_mnemo_hook_command` recognises either shape
so an install of one kind can clean up after the other.
"""
from __future__ import annotations

import argparse
import importlib

from mnemo.cli.parser import command


@command("hook")
def cmd_hook(args: argparse.Namespace) -> int:
    """Run the named hook, swallowing anything it throws.

    Claude Code dispatches hooks on the session's hot path; a traceback here
    would surface as noise in the user's session and, for PreToolUse, could
    read as a denial. The hook modules already guard themselves internally —
    this is the same fail-open contract applied to the dispatch layer that now
    sits between them and Claude Code.
    """
    try:
        module = importlib.import_module(f"mnemo.hooks.{args.event}")
        return int(module.main() or 0)
    except Exception:
        return 0
