"""``mnemo statusline`` + ``mnemo statusline-compose`` — hidden v0.5 entry points."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mnemo.cli.parser import command


@command("statusline")
def cmd_statusline(_args: argparse.Namespace) -> int:
    """Hidden: emit the mnemo statusline segment to stdout."""
    import os
    from mnemo import statusline as sl
    from mnemo.core import config as cfg_mod
    from mnemo.core import paths as paths_mod

    try:
        cfg = cfg_mod.load_config()
        vault = paths_mod.vault_root(cfg)
    except Exception:
        return 0
    claude_json = Path(os.path.expanduser("~/.claude.json"))
    sys.stdout.write(sl.render(vault, claude_json))
    return 0


@command("statusline-compose")
def cmd_statusline_compose(_args: argparse.Namespace) -> int:
    """Hidden: composer that runs the user's original statusLine + mnemo's segment."""
    from mnemo import statusline as sl
    return sl.compose()
