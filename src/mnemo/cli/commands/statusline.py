"""``mnemo statusline`` + ``mnemo statusline-compose`` — hidden v0.5 entry points."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mnemo.cli.parser import command


@command("statusline")
def cmd_statusline(_args: argparse.Namespace) -> int:
    """Emit the mnemo statusline segment, or install/remove the composer.

    Bare, this is the hidden entry point the composer calls on every render.
    With --install it becomes user-facing: Claude Code plugins cannot declare
    a status line, so under a plugin install this is the one piece that needs
    an explicit opt-in rather than arriving with everything else.
    """
    import os
    from mnemo import statusline as sl
    from mnemo.core import config as cfg_mod
    from mnemo.core import paths as paths_mod

    if getattr(_args, "install", False) or getattr(_args, "remove", False):
        return _statusline_wiring(install=bool(getattr(_args, "install", False)))

    try:
        cfg = cfg_mod.load_config()
        vault = paths_mod.vault_root(cfg)
    except Exception:
        return 0
    claude_json = Path(os.path.expanduser("~/.claude.json"))
    # Pass cwd so project-scoped <cwd>/.mcp.json is considered alongside global.
    sys.stdout.write(sl.render(vault, claude_json, cwd=str(Path.cwd())))
    return 0


def _statusline_wiring(*, install: bool) -> int:
    from mnemo.core import config as cfg_mod
    from mnemo.core import paths as paths_mod
    from mnemo.install import settings as inj

    try:
        vault = paths_mod.vault_root(cfg_mod.load_config())
    except Exception as e:
        print(f"Could not resolve the vault: {e}")
        return 1

    settings_path = Path.home() / ".claude" / "settings.json"
    try:
        if install:
            inj.inject_statusline(settings_path, vault)
            print(f"Status line installed in {settings_path}.")
            print("Any status line you already had is preserved and still runs.")
            print("Remove it with: mnemo statusline --remove")
        else:
            inj.uninject_statusline(settings_path, vault)
            print(f"mnemo status line removed from {settings_path}.")
            print("Your previous status line, if any, is restored.")
    except Exception as e:
        print(f"Failed: {e}")
        return 1
    return 0


@command("statusline-compose")
def cmd_statusline_compose(_args: argparse.Namespace) -> int:
    """Hidden: composer that runs the user's original statusLine + mnemo's segment."""
    from mnemo import statusline as sl
    return sl.compose()
