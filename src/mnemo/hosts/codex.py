"""Codex CLI: registration through ``codex mcp add``.

Codex keeps MCP servers in ``~/.codex/config.toml`` (``[mcp_servers.<name>]``
tables). mnemo does not write TOML — the repo supports Python 3.8 and
``tomllib`` is 3.11+ — so it asks the ``codex`` binary to do it and, when
that binary is missing or fails, prints the exact table to paste. Codex has
no per-project MCP config we can rely on, so ``--project`` is refused.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from mnemo._selfexec import self_argv
from mnemo.core.export.writers import Target, target_for
from mnemo.hosts import Host, HostStatus, RegisterResult
from mnemo.hosts.claude import command_exists
from mnemo.install import settings as inj


class CodexScopeError(ValueError):
    """Codex has no project-level MCP config; only global registration exists."""


def _config_path() -> Path:
    return Path(os.path.expanduser("~/.codex/config.toml"))


def _server_argv() -> List[str]:
    return self_argv("mcp-server")


def _add_argv() -> List[str]:
    return ["codex", "mcp", "add", inj.MCPSERVER_NAME, "--", *_server_argv()]


def _remove_argv() -> List[str]:
    return ["codex", "mcp", "remove", inj.MCPSERVER_NAME]


def _toml_escape(value: str) -> str:
    """Escape a value for a TOML basic string (backslashes first, then quotes)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _toml_unescape(value: str) -> str:
    """Reverse ``_toml_escape``: ``\\"`` -> ``"``, ``\\\\`` -> ``\\``."""
    return re.sub(r'\\(["\\])', r"\1", value)


def toml_snippet() -> str:
    argv = _server_argv()
    args = ", ".join(f'"{_toml_escape(a)}"' for a in argv[1:])
    return (
        f"[mcp_servers.{inj.MCPSERVER_NAME}]\n"
        f'command = "{_toml_escape(argv[0])}"\n'
        f"args = [{args}]\n"
    )


def _run(argv: List[str]) -> Optional[str]:
    """None on success, else a one-line reason."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
    return None


_NAME = re.escape(inj.MCPSERVER_NAME)
_TABLE_RE = re.compile(
    r'^[ \t]*\[mcp_servers\.(?:' + _NAME + r'|"' + _NAME + r'")\][ \t]*(?:#.*)?$', re.M
)
_NEXT_TABLE_RE = re.compile(r"^[ \t]*\[", re.M)
_COMMAND_RE = re.compile(r"""^[ \t]*command\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.M)


class CodexHost(Host):
    name = "codex"

    def register_mcp(self, *, project: bool, cwd: Path) -> RegisterResult:
        if project:
            raise CodexScopeError("Codex has no project-level MCP config; omit --project")
        path = str(_config_path())
        if shutil.which("codex") is None:
            note = (f"`codex` is not on PATH — add this to {path} by hand:\n\n"
                    + toml_snippet())
            return RegisterResult(path=path, method="snippet", note=note)
        err = _run(_add_argv())
        if err:
            note = (f"`codex mcp add` failed ({err}) — add this to {path} by hand:\n\n"
                    + toml_snippet())
            return RegisterResult(path=path, method="snippet", note=note)
        return RegisterResult(path=path, method="codex-cli", note=None)

    def unregister_mcp(self, *, project: bool, cwd: Path) -> None:
        if project:
            raise CodexScopeError("Codex has no project-level MCP config; omit --project")
        if shutil.which("codex") is None:
            return
        # Uninstall is best-effort: Host.unregister_mcp -> None, so a failed
        # `codex mcp remove` (already gone, codex changed its CLI, ...) is
        # deliberately swallowed rather than raised.
        _run(_remove_argv())

    def export_target(self, cwd: Path) -> Target:
        return target_for("codex", "auto", Path(cwd))

    def describe(self, *, project: bool, cwd: Path) -> HostStatus:
        path = _config_path()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return HostStatus(name=self.name, registered=False, path=str(path), command_ok=False, detail="")
        m = _TABLE_RE.search(text)
        if not m:
            return HostStatus(name=self.name, registered=False, path=str(path), command_ok=False, detail="")
        # The table's own lines run until the next ``[`` header.
        rest = text[m.end():]
        nxt = _NEXT_TABLE_RE.search(rest)
        body = rest[: nxt.start()] if nxt else rest
        cm = _COMMAND_RE.search(body)
        if cm:
            raw = cm.group(1) if cm.group(1) is not None else cm.group(2)
            # group(1) is a basic string ("...", escaped); group(2) is a
            # literal string ('...', verbatim) — only un-escape the former.
            command = _toml_unescape(raw) if cm.group(1) is not None else raw
        else:
            command = ""
        ok = command_exists(command)
        detail = "" if ok else f"command not found: {command or '(missing)'}"
        return HostStatus(name=self.name, registered=True, path=str(path), command_ok=ok, detail=detail)
