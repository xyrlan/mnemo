"""Host adapters — where mnemo registers its MCP server and drops a rules file.

mnemo's core is host-neutral (vault, extraction, indexes, the stdio MCP
server). What binds it to Claude Code is a thin layer; this package holds
the part of that layer another tool can use today: MCP registration and
the rules file `mnemo export` writes. Hooks and transcript readers stay
Claude-only (issue #127 tracks the rest).

Each host answers four questions and nothing more:

- ``register_mcp`` / ``unregister_mcp``: put the server in the host's config
- ``export_target``: where the rules file goes in a repo
- ``describe``: is it wired, and does the registered command exist?
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from mnemo.core.export.writers import Target


@dataclass(frozen=True)
class RegisterResult:
    path: str            # file the registration lives in (or would live in)
    method: str          # "json" | "codex-cli" | "snippet"
    note: Optional[str]  # something the user must do by hand, else None


@dataclass(frozen=True)
class HostStatus:
    name: str
    registered: bool
    path: str
    command_ok: bool     # the registered command exists on disk
    detail: str          # one short human line, may be empty


class Host:
    """Base class; subclasses set ``name`` and implement the four methods."""

    name: str = ""

    def register_mcp(self, *, project: bool, cwd: Path) -> RegisterResult:
        raise NotImplementedError

    def unregister_mcp(self, *, project: bool, cwd: Path) -> None:
        raise NotImplementedError

    def export_target(self, cwd: Path) -> Target:
        raise NotImplementedError

    def describe(self, *, project: bool, cwd: Path) -> HostStatus:
        raise NotImplementedError


def _build_registry() -> Dict[str, Host]:
    from mnemo.hosts.claude import ClaudeHost
    from mnemo.hosts.codex import CodexHost
    from mnemo.hosts.cursor import CursorHost

    return {h.name: h for h in (ClaudeHost(), CursorHost(), CodexHost())}


HOSTS: Dict[str, Host] = {}


def get_host(name: str) -> Host:
    if not HOSTS:
        HOSTS.update(_build_registry())
    return HOSTS[name]


# Populate eagerly so ``list(HOSTS)`` is stable for argparse choices.
HOSTS.update(_build_registry())
