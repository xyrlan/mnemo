"""Host adapters — where mnemo registers its MCP server and drops a rules file.

mnemo's core is host-neutral (vault, extraction, indexes, the stdio MCP
server). What binds it to Claude Code is a thin layer; this package holds
the part of that layer another tool can use today: MCP registration and
the rules file `mnemo export` writes. Hooks and transcript readers stay
Claude-only (issue #127 tracks the rest).

Each host implements four methods and nothing more:

- ``register_mcp`` / ``unregister_mcp``: put the server in the host's config
- ``export_target``: where the rules file goes in a repo
- ``describe``: is it wired, and does the registered command exist?
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, Optional

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
    command_ok: bool     # the registered command exists (file on disk, or resolves on PATH)
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


HOSTS: Dict[str, Host] = _build_registry()


def get_host(name: str) -> Host:
    return HOSTS[name]


def registered_hosts(cwd: Path) -> Iterator[HostStatus]:
    """Every registered non-Claude host, global and project scope, deduped.

    Claude Code is the default host with its own status lines elsewhere, so
    it is skipped here. A host that ignores ``project`` (codex has no
    per-project MCP config) reports the same path for both scopes; dedupe by
    ``(name, path)`` so it is not yielded twice. Per-host errors — a
    malformed config, a host module that fails to import — are swallowed: a
    status/doctor line is not worth a traceback.
    """
    seen = set()
    for name, host in HOSTS.items():
        if name == "claude":
            continue
        for project in (False, True):
            try:
                status = host.describe(project=project, cwd=cwd)
            except Exception:  # noqa: BLE001
                continue
            if not status.registered:
                continue
            key = (name, status.path)
            if key in seen:
                continue
            seen.add(key)
            yield status
