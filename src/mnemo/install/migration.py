"""Migrating a pre-plugin install off settings.json.

Anyone who ran ``mnemo init`` has four hooks in ``settings.json``. Installing
the plugin adds four more through the plugin's own ``hooks.json``, and Claude
Code fires both: doubled capture, doubled injection, doubled enforcement.

A plugin cannot write outside its own directory, so this cannot be repaired
silently. The plugin's SessionStart notices the overlap and says so once; the
user runs ``/mnemo:migrate`` to clear it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from mnemo.install.settings import is_mnemo_hook_command, uninject_hooks

NOTICE_MARKER = "plugin-migration-notified"


def _hook_commands(settings_path: Path) -> list[str]:
    try:
        data = json.loads(settings_path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []
    return [
        h.get("command", "")
        for entries in hooks.values()
        if isinstance(entries, list)
        for entry in entries
        if isinstance(entry, dict)
        for h in (entry.get("hooks") or [])
        if isinstance(h, dict)
    ]


def find_legacy_installs(settings_paths: Iterable[Path]) -> list[Path]:
    """Return the settings files that still carry mnemo hooks.

    Under the plugin, *any* mnemo hook in settings.json is a leftover: plugin
    hooks live in the plugin's own hooks.json and never appear here.
    """
    found = []
    for path in settings_paths:
        path = Path(path)
        if not path.exists():
            continue
        if any(is_mnemo_hook_command(c) for c in _hook_commands(path)):
            found.append(path)
    return found


def migrate(settings_paths: Iterable[Path]) -> list[Path]:
    """Strip mnemo hooks from each path. Returns the ones actually changed.

    Delegates to :func:`uninject_hooks`, which already backs the file up, holds
    the settings lock, and preserves entries that mix mnemo hooks with someone
    else's.
    """
    changed = []
    for path in find_legacy_installs(settings_paths):
        uninject_hooks(path)
        changed.append(path)
    return changed


def should_notify(state_path: Path) -> bool:
    """True until :func:`mark_notified` has run — the notice fires once."""
    return not Path(state_path).exists()


def mark_notified(state_path: Path) -> None:
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(NOTICE_MARKER)


def notice(settings_paths: Iterable[Path]) -> str:
    paths = "\n".join(f"  - {p}" for p in settings_paths)
    return (
        "mnemo is installed twice: once by the plugin, and once directly in\n"
        f"{paths}\n"
        "Both sets of hooks fire, so capture and rule injection are duplicated.\n"
        "Run /mnemo:migrate to remove the direct install. Your vault is untouched."
    )
