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
from mnemo.core.activity import activities_for


def _consume_unblocks() -> int:
    """``mnemo sessions --consume-unblocks`` — redeem the recorded markers.

    The production reader ``detector.pending_unblocks`` never had (#195). The
    ``session_end`` hook spawns this detached; a maintainer can also run it by
    hand, which is why it reports rather than staying silent.

    Unscoped, like the sweep that records the markers: an unblocked session is
    worth learning from wherever it ran.
    """
    from mnemo import cli  # late binding, as the queue path does
    from mnemo.core import config as cfg_mod
    from mnemo.core.sessions import unblocks

    report = unblocks.consume(cfg_mod.load_config(), vault_root=cli._resolve_vault())

    if not (report.consumed or report.failed or report.skipped):
        print("no unblocked session waiting to be learned from")
        return 0

    print(f"consumed: {report.consumed} unblocked session(s)")
    for entry in report.learned:
        print(f"learned: {entry.get('slug')} — {entry.get('name')}")
    if report.skipped:
        print(f"skipped: {report.skipped} marker(s) with no resolvable transcript")
    for line in report.errors:
        # Left pending on purpose: these are retried on the next pass.
        print(f"deferred: {line}")
    return 0


@command("sessions")
def cmd_sessions(args: argparse.Namespace) -> int:
    """Print background sessions, blocked first."""
    import json as _json
    import os
    import sys
    import time
    from dataclasses import asdict

    from mnemo.core.sessions.jobs import normalize_cwd, read_sessions
    from mnemo.core.sessions.render import render_queue

    if bool(getattr(args, "consume_unblocks", False)):
        return _consume_unblocks()

    # Normalized here too: the stored cwd may be the canonical form while the
    # live one arrives through a worktree symlink. See jobs.normalize_cwd.
    scope = None if getattr(args, "all", False) else normalize_cwd(os.getcwd())

    def _read():
        found = read_sessions(cwd=scope)
        try:
            from mnemo import cli  # late binding for monkeypatched _resolve_vault
            from mnemo.core.sessions import detector

            detector.sweep(found, vault_root=cli._resolve_vault())
        except Exception:
            pass  # the queue must print even when the vault is unavailable
        return found

    if bool(getattr(args, "json", False)):
        print(_json.dumps([asdict(s) for s in _read()], indent=2, ensure_ascii=False))
        return 0

    # In memory, for the life of this process. An offset only has value inside
    # a live watch; a single invocation wants current state, not a delta.
    #
    # `previous` is a one-element list, not a bare dict, because `_activities`
    # both reads and replaces it on every tick. A plain name would need
    # `nonlocal`; the list keeps the closure honest with one fewer keyword.
    offsets = {}
    previous = [{}]

    def _activities(found):
        """Never let a transcript read cost us the queue itself."""
        try:
            previous[0] = activities_for(found, offsets, previous=previous[0])
        except Exception:
            previous[0] = {}
        return previous[0]

    if bool(getattr(args, "watch", False)):
        # Only a terminal understands the escape; redirected to a log it would
        # be raw bytes on every redraw.
        clear = "\033[2J\033[H" if sys.stdout.isatty() else ""  # clear + home
        try:
            while True:
                found = _read()
                acts = _activities(found)
                print(clear, end="")
                print(render_queue(found, acts))
                time.sleep(2)
        except KeyboardInterrupt:
            return 0

    found = _read()
    print(render_queue(found, _activities(found)))
    return 0
