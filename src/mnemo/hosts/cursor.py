"""Cursor: same JSON shape as Claude Code, different paths (Task 3)."""
from __future__ import annotations

from mnemo.hosts import Host


class CursorHost(Host):
    name = "cursor"
