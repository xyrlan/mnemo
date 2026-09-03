"""Claude Code: the host mnemo was built for. Wraps the existing writers."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Tuple

from mnemo.core.export.writers import Target, target_for
from mnemo.hosts import Host, HostStatus, RegisterResult
from mnemo.install import settings as inj


def _mcp_path(project: bool, cwd: Path) -> Path:
    if project:
        return Path(cwd) / ".mcp.json"
    return Path(os.path.expanduser("~/.claude.json"))


def command_exists(command: str) -> bool:
    """True for an existing executable file, or a bare name that resolves on PATH."""
    if not command:
        return False
    if os.sep in command or (os.altsep and os.altsep in command) or os.path.isabs(command):
        return Path(command).is_file() and os.access(command, os.X_OK)
    return shutil.which(command) is not None


def json_registration(path: Path) -> Tuple[bool, str]:
    """(registered, command) for a ``mcpServers`` JSON file; tolerant of garbage."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, ""
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    entry = servers.get(inj.MCPSERVER_NAME) if isinstance(servers, dict) else None
    if not isinstance(entry, dict):
        return False, ""
    cmd = entry.get("command")
    return True, cmd if isinstance(cmd, str) else ""


def json_host_status(name: str, path: Path) -> HostStatus:
    """Build a ``HostStatus`` from a ``mcpServers`` JSON file at ``path``."""
    registered, command = json_registration(path)
    ok = command_exists(command) if registered else False
    if not registered:
        detail = ""
    elif not command:
        detail = "registration has no command"
    elif not ok:
        detail = f"command not found: {command}"
    else:
        detail = ""
    return HostStatus(name=name, registered=registered, path=str(path),
                      command_ok=ok, detail=detail)


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
        return json_host_status(self.name, _mcp_path(project, cwd))
