"""Inject mnemo hooks into ~/.claude/settings.json (and the v0.5 MCP server into ~/.claude.json).

Two parallel injection flows live here:

- ``inject_hooks`` / ``uninject_hooks`` write the SessionStart + SessionEnd
  command hooks into ``~/.claude/settings.json`` (under the ``hooks`` key).
- ``inject_mcp_servers`` / ``uninject_mcp_servers`` (v0.5) write the mnemo
  MCP stdio server entry into ``~/.claude.json`` (under ``mcpServers``).
  These are *different files* — Claude Code reads hooks from settings.json
  but reads MCP servers from .claude.json at the home root.

Both flows share the same lock + backup primitives below.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from mnemo.core import locks

# Marker substring used to identify mnemo entries in settings.json. The tag
# must be a literal substring of every valid hook command we generate so that
# uninject_hooks can find them — but it must NOT prepend or otherwise corrupt
# the command, or Claude Code will fail to dispatch the hook. The python -m
# target naturally contains "mnemo.hooks." in every command we emit, which
# makes it the perfect marker: zero collision risk and zero impact on
# executability.
MNEMO_TAG = "mnemo.hooks."


class SettingsError(Exception):
    pass


def _hook_command(module: str) -> str:
    """Return the command line that invokes a mnemo hook."""
    return f"{sys.executable or 'python3'} -m mnemo.hooks.{module}"


HOOK_DEFINITIONS: dict[str, dict[str, Any]] = {
    "SessionStart": {
        "module": "session_start",
        "matcher": None,
        "async": False,
    },
    "PreToolUse": {
        "module": "pre_tool_use",
        "matcher": "Bash|Edit|Write|MultiEdit",
        "async": False,
    },
    "SessionEnd": {
        "module": "session_end",
        "matcher": None,
        "async": False,
    },
}


def _build_entry(event: str, defn: dict[str, Any]) -> dict[str, Any]:
    hook: dict[str, Any] = {"type": "command", "command": _hook_command(defn["module"])}
    if defn.get("async"):
        hook["async"] = True
    entry: dict[str, Any] = {"hooks": [hook]}
    if defn.get("matcher"):
        entry["matcher"] = defn["matcher"]
    return entry


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SettingsError(
            f"Cannot parse {path}. mnemo refuses to overwrite a malformed settings.json. "
            f"Fix the JSON or remove the file and re-run /mnemo init. ({e})"
        )
    if not isinstance(data, dict):
        raise SettingsError(f"{path} root must be a JSON object")
    return data


def _strip_mnemo_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove every entry whose hook list is entirely mnemo commands; preserve mixed entries."""
    cleaned: list[dict[str, Any]] = []
    for entry in entries:
        hooks = entry.get("hooks", [])
        non_mnemo = [h for h in hooks if MNEMO_TAG not in h.get("command", "")]
        if non_mnemo:
            new = dict(entry)
            new["hooks"] = non_mnemo
            cleaned.append(new)
        # else: drop the whole entry — it was 100% mnemo
    return cleaned


def _backup(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    backup.write_text(path.read_text())


def _with_lock(path: Path):
    return locks.try_lock(path.parent / ".mnemo-settings.lock")


def inject_hooks(settings_path: Path) -> None:
    settings_path = Path(settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 5.0
    while True:
        with _with_lock(settings_path) as held:
            if held:
                _do_inject(settings_path)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_inject(settings_path: Path) -> None:
    data = _read_settings(settings_path)
    _backup(settings_path)
    hooks = data.setdefault("hooks", {})

    # Legacy migration: strip mnemo entries from ALL hook events, not just
    # those in HOOK_DEFINITIONS. Prunes previously-installed hooks that have
    # since been removed (e.g. UserPromptSubmit, PostToolUse from v0.3.1).
    # If an event ends up with no remaining hooks after stripping, drop it.
    for event in list(hooks.keys()):
        hooks[event] = _strip_mnemo_entries(hooks[event])
        if not hooks[event]:
            del hooks[event]

    # Re-register current hooks
    for event, defn in HOOK_DEFINITIONS.items():
        existing = hooks.get(event, [])
        existing.append(_build_entry(event, defn))
        hooks[event] = existing

    settings_path.write_text(json.dumps(data, indent=2))


def uninject_hooks(settings_path: Path) -> None:
    settings_path = Path(settings_path)
    if not settings_path.exists():
        return
    deadline = time.time() + 5.0
    while True:
        with _with_lock(settings_path) as held:
            if held:
                _do_uninject(settings_path)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_uninject(settings_path: Path) -> None:
    data = _read_settings(settings_path)
    _backup(settings_path)
    hooks = data.get("hooks", {})
    for event in list(HOOK_DEFINITIONS):
        if event in hooks:
            cleaned = _strip_mnemo_entries(hooks[event])
            if cleaned:
                hooks[event] = cleaned
            else:
                hooks.pop(event)
    if not hooks:
        data.pop("hooks", None)
    settings_path.write_text(json.dumps(data, indent=2))


# --- v0.5: MCP server registration in ~/.claude.json ---


def _mcp_server_spec() -> dict[str, Any]:
    """Build the mcpServers entry for the mnemo stdio server.

    Uses ``sys.executable`` so the registration points at the same Python
    interpreter that ran ``mnemo init`` — important when mnemo is installed
    in a venv that isn't first on PATH.
    """
    return {
        "command": sys.executable or "python3",
        "args": ["-m", "mnemo", "mcp-server"],
    }


MCPSERVER_NAME = "mnemo"


def inject_mcp_servers(claude_json_path: Path) -> None:
    """Register the mnemo MCP server in ``~/.claude.json``. Idempotent."""
    claude_json_path = Path(claude_json_path)
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 5.0
    while True:
        with _with_lock(claude_json_path) as held:
            if held:
                _do_inject_mcp(claude_json_path)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for .claude.json lock (5s)")
        time.sleep(0.05)


def _do_inject_mcp(claude_json_path: Path) -> None:
    data = _read_settings(claude_json_path)
    _backup(claude_json_path)
    servers = data.setdefault("mcpServers", {})
    servers[MCPSERVER_NAME] = _mcp_server_spec()
    claude_json_path.write_text(json.dumps(data, indent=2))


def uninject_mcp_servers(claude_json_path: Path) -> None:
    """Remove the mnemo MCP server entry from ``~/.claude.json``. No-op if absent."""
    claude_json_path = Path(claude_json_path)
    if not claude_json_path.exists():
        return
    deadline = time.time() + 5.0
    while True:
        with _with_lock(claude_json_path) as held:
            if held:
                _do_uninject_mcp(claude_json_path)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for .claude.json lock (5s)")
        time.sleep(0.05)


def _do_uninject_mcp(claude_json_path: Path) -> None:
    data = _read_settings(claude_json_path)
    _backup(claude_json_path)
    servers = data.get("mcpServers", {})
    servers.pop(MCPSERVER_NAME, None)
    if not servers:
        data.pop("mcpServers", None)
    claude_json_path.write_text(json.dumps(data, indent=2))


# --- v0.5: statusLine additive composer registration ---


def _statusline_compose_command() -> str:
    """Build the composer command line. Uses sys.executable for venv correctness."""
    return f"{sys.executable or 'python3'} -m mnemo statusline-compose"


def _is_mnemo_composer(spec: Any) -> bool:
    """True if the given statusLine entry is our composer (so re-init is a no-op)."""
    if not isinstance(spec, dict):
        return False
    cmd = spec.get("command", "")
    if not isinstance(cmd, str):
        return False
    return cmd.strip().endswith("statusline-compose")


def inject_statusline(settings_path: Path, vault_root: Path) -> None:
    """Install the additive statusLine composer in ``~/.claude/settings.json``.

    If the user has a pre-existing statusLine, it's preserved in
    ``<vault>/.mnemo/statusline-original.json`` and the composer wraps it.
    Re-running ``mnemo init`` is a no-op when the composer is already
    installed (the original is captured exactly once).
    """
    settings_path = Path(settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 5.0
    while True:
        with _with_lock(settings_path) as held:
            if held:
                _do_inject_statusline(settings_path, vault_root)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_inject_statusline(settings_path: Path, vault_root: Path) -> None:
    from mnemo import statusline as sl_mod

    data = _read_settings(settings_path)
    _backup(settings_path)
    existing = data.get("statusLine")

    if _is_mnemo_composer(existing):
        # Already installed — do not re-capture original (it's already saved).
        return

    # Capture original (which may be absent or anything else) into mnemo state.
    if existing is None:
        sl_mod.write_state(vault_root, None)
    elif isinstance(existing, dict):
        sl_mod.write_state(vault_root, existing)
    else:
        # Unknown shape — coerce into a string command for best-effort restore.
        sl_mod.write_state(vault_root, {"command": str(existing)})

    data["statusLine"] = {
        "type": "command",
        "command": _statusline_compose_command(),
    }
    settings_path.write_text(json.dumps(data, indent=2))


def uninject_statusline(settings_path: Path, vault_root: Path) -> None:
    """Restore the user's original statusLine and clear mnemo state."""
    settings_path = Path(settings_path)
    if not settings_path.exists():
        return
    deadline = time.time() + 5.0
    while True:
        with _with_lock(settings_path) as held:
            if held:
                _do_uninject_statusline(settings_path, vault_root)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_uninject_statusline(settings_path: Path, vault_root: Path) -> None:
    from mnemo import statusline as sl_mod

    data = _read_settings(settings_path)
    _backup(settings_path)
    current = data.get("statusLine")

    if not _is_mnemo_composer(current):
        # Not our composer — leave whatever the user has alone.
        sl_mod.clear_state(vault_root)
        return

    state = sl_mod.read_state(vault_root)
    if state and state.get("command"):
        data["statusLine"] = {
            "type": state.get("type") or "command",
            "command": state["command"],
        }
    else:
        data.pop("statusLine", None)

    sl_mod.clear_state(vault_root)
    settings_path.write_text(json.dumps(data, indent=2))
