"""Cursor: same ``mcpServers`` JSON shape as Claude Code, different paths.

Global config is ``~/.cursor/mcp.json``; a project can carry its own
``.cursor/mcp.json``. Rules are ``.mdc`` files under ``.cursor/rules/`` with
mandatory frontmatter -- ``mnemo export --host cursor`` writes that.
"""
from __future__ import annotations

import os
from pathlib import Path

from mnemo.core.export.writers import Target, target_for
from mnemo.hosts import Host, HostStatus, RegisterResult
from mnemo.hosts.claude import json_host_status
from mnemo.install import settings as inj


def _mcp_path(project: bool, cwd: Path) -> Path:
    if project:
        return Path(cwd) / ".cursor" / "mcp.json"
    return Path(os.path.expanduser("~/.cursor/mcp.json"))


class CursorHost(Host):
    name = "cursor"

    def register_mcp(self, *, project: bool, cwd: Path) -> RegisterResult:
        path = _mcp_path(project, cwd)
        inj.inject_mcp_servers(path)
        return RegisterResult(path=str(path), method="json", note=None)

    def unregister_mcp(self, *, project: bool, cwd: Path) -> None:
        inj.uninject_mcp_servers(_mcp_path(project, cwd))

    def export_target(self, cwd: Path) -> Target:
        return target_for("cursor", "auto", Path(cwd))

    def describe(self, *, project: bool, cwd: Path) -> HostStatus:
        return json_host_status(self.name, _mcp_path(project, cwd))
