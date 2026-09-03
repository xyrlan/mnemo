"""Claude Code: the host mnemo was built for. Wraps the existing writers."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from mnemo.core.export.writers import Target, target_for
from mnemo.hosts import Host, HostStatus, RegisterResult
from mnemo.install import settings as inj


def _mcp_path(project: bool, cwd: Path) -> Path:
    if project:
        return Path(cwd) / ".mcp.json"
    return Path(os.path.expanduser("~/.claude.json"))


def command_exists(command: str) -> bool:
    """True when ``command`` is an existing file or resolves on PATH."""
    if not command:
        return False
    return Path(command).exists() or shutil.which(command) is not None


def json_registration(path: Path) -> tuple:
    """(registered, command) for a ``mcpServers`` JSON file; tolerant of garbage."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, ""
    entry = (data.get("mcpServers") or {}).get(inj.MCPSERVER_NAME) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return False, ""
    return True, str(entry.get("command") or "")


class ClaudeHost(Host):
    name = "claude"

    def register_mcp(self, *, project: bool, cwd: Path) -> RegisterResult:
        path = _mcp_path(project, cwd)
        inj.inject_mcp_servers(path)
        return RegisterResult(path=str(path), method="json", note=None)

    def unregister_mcp(self, *, project: bool, cwd: Path) -> None:
        inj.uninject_mcp_servers(_mcp_path(project, cwd))

    def export_target(self, cwd: Path) -> Target:
        return target_for("claude", "auto", Path(cwd))

    def describe(self, *, project: bool, cwd: Path) -> HostStatus:
        path = _mcp_path(project, cwd)
        registered, command = json_registration(path)
        ok = command_exists(command) if registered else False
        detail = "" if (not registered or ok) else f"command not found: {command}"
        return HostStatus(name=self.name, registered=registered, path=str(path),
                          command_ok=ok, detail=detail)
