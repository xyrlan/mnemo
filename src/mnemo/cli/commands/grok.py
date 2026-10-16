"""``mnemo grok``: Grok Build sessions through the leader's ACP socket.

A seam of the Grok round (``design/specs/2026-10-16-grok-host.md``): the
parser and this handler exist so the pieces can be written against them; the
``grok-acp`` piece of ``design/contracts/2026-10-16-grok-host.md`` fills it in.
Until then every action says so and exits 2.
"""
from __future__ import annotations

import argparse
import sys

from mnemo.cli.parser import command


@command("grok")
def cmd_grok(args: argparse.Namespace) -> int:
    print(f"mnemo grok {args.grok_action}: not built yet "
          "(design/contracts/2026-10-16-grok-host.md, piece grok-acp)",
          file=sys.stderr)
    return 2
