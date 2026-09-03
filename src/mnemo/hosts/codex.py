"""Codex CLI: registration through ``codex mcp add`` (Task 4)."""
from __future__ import annotations

from mnemo.hosts import Host


class CodexHost(Host):
    name = "codex"
