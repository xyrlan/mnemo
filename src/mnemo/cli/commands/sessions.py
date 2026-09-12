"""``mnemo sessions`` — the live queue of Claude Code background sessions.

Six sessions in parallel cost six streams of attention unless something says
which ones are actually waiting. This prints that, blocked first.

Deliberately human-only: no hook and no MCP tool exposes it. The queue must
never spend a token of the parent session's context — that context is the
scarce resource the whole feature exists to protect.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("sessions")
def cmd_sessions(args: argparse.Namespace) -> int:
    """Print background sessions, blocked first."""
    import json as _json
    import os
    import time
    from dataclasses import asdict

    from mnemo.core.sessions.jobs import read_sessions
    from mnemo.core.sessions.render import render_queue

    scope = None if getattr(args, "all", False) else os.getcwd()

    def _read():
        return read_sessions(cwd=scope)

    if bool(getattr(args, "json", False)):
        print(_json.dumps([asdict(s) for s in _read()], indent=2, ensure_ascii=False))
        return 0

    if bool(getattr(args, "watch", False)):
        try:
            while True:
                print("\033[2J\033[H", end="")  # clear + home
                print(render_queue(_read()))
                time.sleep(2)
        except KeyboardInterrupt:
            return 0

    print(render_queue(_read()))
    return 0
