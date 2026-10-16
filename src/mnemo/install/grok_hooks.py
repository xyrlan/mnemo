"""mnemo's hooks in a Grok hook file, for a machine whose Grok cannot read them from Claude.

Grok reads ``~/.claude/settings.json`` as one of its hook sources, so a machine
with mnemo installed for Claude Code already fires mnemo's hooks in Grok
sessions (``design/specs/2026-10-16-grok-host.md``, decision 7). This module is
for the other machine: Grok alone, or Grok with Claude's hooks switched off
(``[compat.claude] hooks = false``). There the same four commands go in a hook
file Grok owns: ``$GROK_HOME/hooks/mnemo.json`` for the account, or
``<project>/.grok/hooks/mnemo.json`` for one repo.

The file is mnemo's alone and holds nothing else, so it is rewritten whole; a
hand-edited one is kept apart from mnemo's entries on removal. The commands and
matchers are ``settings.HOOK_DEFINITIONS``' (one table), and Grok's matcher
syntax accepts Claude's tool names, so ``Bash|Read|Edit|Write|MultiEdit`` still
selects ``run_terminal_command``, ``read_file`` and the edit tools.

Whether to write the file at all is decided by the caller from what Grok says
it loads (``grok inspect --json``): a hook Grok already sees must not be added
twice.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from mnemo.core.atomic import atomic_write_bytes
from mnemo.install import settings as inj

FILE_NAME = "mnemo.json"


def hook_file(home: Path, *, project: bool = False, cwd: Optional[Path] = None) -> Path:
    """Where mnemo's Grok hook file lives: the account's, or one project's."""
    if project:
        return Path(cwd if cwd is not None else Path.cwd()) / ".grok" / "hooks" / FILE_NAME
    return Path(home) / "hooks" / FILE_NAME


def grok_event_name(event: str) -> str:
    """``PreToolUse`` -> ``pre_tool_use``: how ``grok inspect --json`` names an event."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def expected_events() -> List[str]:
    """The Grok event names mnemo's hooks cover, in install order."""
    return [grok_event_name(event) for event in inj.HOOK_DEFINITIONS]


def render() -> Dict[str, Any]:
    """The hook file's content: ``settings.json``'s mnemo entries under ``hooks``."""
    return {"hooks": {event: [inj._build_entry(event, defn)]
                      for event, defn in inj.HOOK_DEFINITIONS.items()}}


def _read(path: Path) -> Optional[Dict[str, Any]]:
    """The file's JSON object, ``{}`` when absent, ``None`` when it is not one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_installed(path: Path) -> bool:
    """True when ``path`` holds at least one mnemo hook command."""
    data = _read(path)
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return False
    for entries in hooks.values():
        for entry in entries if isinstance(entries, list) else []:
            for hook in (entry or {}).get("hooks", []) or []:
                if inj.is_mnemo_hook_command((hook or {}).get("command", "")):
                    return True
    return False


def install(path: Path) -> None:
    """Write mnemo's hooks to ``path``, replacing a stale copy of them.

    Entries in the file that are not mnemo's are kept; a file that is not a
    JSON object is refused rather than overwritten.
    """
    path = Path(path)
    data = _read(path)
    if data is None:
        raise inj.SettingsError(f"{path} is not a JSON object; move it aside and re-run")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise inj.SettingsError(f"{path}: \"hooks\" is not an object; fix it by hand")
    for event in list(hooks):
        entries = hooks[event]
        if isinstance(entries, list):
            hooks[event] = inj._strip_mnemo_entries(entries)
            if not hooks[event]:
                del hooks[event]
    for event, entries in render()["hooks"].items():
        hooks.setdefault(event, []).extend(entries)
    _write(path, data)


def uninstall(path: Path) -> bool:
    """Remove mnemo's hooks from ``path``; True when something was removed.

    A file left with nothing in it is deleted, so an uninstall leaves no empty
    ``mnemo.json`` behind.
    """
    path = Path(path)
    data = _read(path)
    if not data or not is_installed(path):
        return False
    hooks = data["hooks"]
    for event in list(hooks):
        if isinstance(hooks[event], list):
            hooks[event] = inj._strip_mnemo_entries(hooks[event])
            if not hooks[event]:
                del hooks[event]
    if not hooks:
        data.pop("hooks")
    if data:
        _write(path, data)
    else:
        path.unlink()
    return True


def _write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
