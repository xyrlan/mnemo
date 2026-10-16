"""Grok Build: MCP through ``grok mcp add``, and a look at what Grok already loads.

Grok keeps MCP servers in ``$GROK_HOME/config.toml`` (``[mcp_servers.<name>]``,
the same table Codex uses), and also *reads* Claude Code's ``~/.claude.json``
and ``~/.claude/settings.json`` hooks by default
(``design/specs/2026-10-16-grok-host.md``, Hooks). So on a machine where mnemo
is wired for Claude, a Grok session already gets mnemo's MCP server and hooks;
adding a second copy would run each hook twice. ``grok inspect --json`` is
Grok's own account of what it loads, and is what this module asks before it
writes anything: a server or hook it already lists is left alone.

Like Codex, mnemo does not write TOML (the repo supports Python 3.8, and
``tomllib`` is 3.11+). The ``grok`` binary writes its own config, and when it
is missing or fails the exact table to paste is printed instead.

One Grok home is one account (``$GROK_HOME``, default ``~/.grok``); mnemo wires
the home this process would use.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mnemo._selfexec import self_argv
from mnemo.core.export.writers import Target, target_for
from mnemo.core.grok import paths
from mnemo.hosts import Host, HostStatus, RegisterResult
from mnemo.hosts.claude import command_exists, json_registration
from mnemo.hosts.codex import (
    _COMMAND_RE, _NEXT_TABLE_RE, _TABLE_RE, _toml_unescape, toml_snippet,
)
from mnemo.install import grok_hooks
from mnemo.install import settings as inj

#: How long ``grok inspect`` / ``grok mcp`` may take. Both are local and quick
#: (~0.1 s measured on 1.0.60); this only bounds a wedged binary.
TIMEOUT_S = 20


def grok_on_path() -> Optional[str]:
    return shutil.which("grok")


def run_grok(args: List[str], *, home: Path, cwd: Path) -> Tuple[Optional[str], str]:
    """Run ``grok <args>`` against ``home``: ``(error or None, stdout)``.

    The one place this module starts ``grok``. ``GROK_HOME`` is set so the call
    lands in the account being wired, not whichever one the shell exports.
    """
    env = dict(os.environ)
    env[paths.HOME_ENV] = str(home)
    try:
        proc = subprocess.run(["grok", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT_S,
                              env=env, cwd=str(cwd))
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc), ""
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip(), ""
    return None, proc.stdout or ""


def inspect(home: Path, cwd: Path) -> Optional[Dict[str, Any]]:
    """``grok inspect --json`` for ``home`` and ``cwd``, or ``None`` when unavailable."""
    if grok_on_path() is None:
        return None
    err, out = run_grok(["inspect", "--json"], home=home, cwd=cwd)
    if err:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _enabled(item: Dict[str, Any]) -> bool:
    # Native entries carry no status; a vendor one says "enabled" or why not
    # (`[compat.claude] hooks = false` turns every Claude hook off).
    return item.get("compatibilityStatus", "enabled") == "enabled"


def visible_hooks(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The mnemo hooks Grok lists as loaded, whichever file they come from."""
    return [h for h in data.get("hooks") or []
            if isinstance(h, dict) and _enabled(h)
            and inj.is_mnemo_hook_command(str(h.get("target") or ""))]


def visible_mcp(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Grok's entry for the mnemo MCP server, or ``None`` when it lists none."""
    for server in data.get("mcpServers") or []:
        if isinstance(server, dict) and server.get("name") == inj.MCPSERVER_NAME and _enabled(server):
            return server
    return None


def source_path(item: Dict[str, Any]) -> str:
    """The file an ``inspect`` entry was read from, '' when it does not say."""
    source = item.get("source")
    return str(source.get("path") or "") if isinstance(source, dict) else ""


def hook_events(hooks: List[Dict[str, Any]]) -> List[str]:
    """The distinct Grok event names of ``hooks``, in the order mnemo installs them."""
    seen = {str(h.get("event")) for h in hooks}
    return [e for e in grok_hooks.expected_events() if e in seen]


def _config_path(project: bool, cwd: Path, home: Path) -> Path:
    return Path(cwd) / ".grok" / "config.toml" if project else Path(home) / "config.toml"


def _scope(project: bool) -> str:
    return "project" if project else "user"


class GrokHost(Host):
    name = "grok"

    def register_mcp(self, *, project: bool, cwd: Path) -> RegisterResult:
        cwd = Path(cwd)
        home = paths.grok_home()
        path = str(_config_path(project, cwd, home))
        if not project:
            inherited = self._inherited_mcp(home)
            if inherited:
                return RegisterResult(path=inherited, method="inherited", note=None)
        if grok_on_path() is None:
            note = (f"`grok` is not on PATH — add this to {path} by hand:\n\n" + toml_snippet())
            return RegisterResult(path=path, method="snippet", note=note)
        argv = ["mcp", "add", inj.MCPSERVER_NAME, "-s", _scope(project), "--",
                *self_argv("mcp-server")]
        err, _ = run_grok(argv, home=home, cwd=cwd)
        if err:
            note = (f"`grok mcp add` failed ({err}) — add this to {path} by hand:\n\n"
                    + toml_snippet())
            return RegisterResult(path=path, method="snippet", note=note)
        return RegisterResult(path=path, method="grok-cli", note=None)

    @staticmethod
    def _inherited_mcp(home: Path) -> str:
        """The file Grok already reads mnemo's MCP server from, or ''.

        ``grok inspect`` is the source of truth. Without the binary there is
        nothing to wire either way, so Claude's own registration is read
        directly: the machine is then ready for the day ``grok`` is installed.
        Asked from the home directory, where Grok loads no project ``.mcp.json``:
        a server one repo carries is not one every session would get.
        """
        data = inspect(home, Path.home())
        if data is not None:
            server = visible_mcp(data)
            return source_path(server) if server else ""
        claude_json = Path(os.path.expanduser("~/.claude.json"))
        return str(claude_json) if json_registration(claude_json)[0] else ""

    def unregister_mcp(self, *, project: bool, cwd: Path) -> None:
        """Remove what ``register_mcp`` and ``mnemo init --host grok`` added.

        That is the config entry Grok itself wrote and mnemo's hook file. Claude's
        own ``~/.claude.json`` and ``~/.claude/settings.json`` are never touched:
        they are Claude's install, removed by ``mnemo uninstall``. Best-effort
        like Codex's: ``Host.unregister_mcp`` returns nothing, so a missing
        binary or an entry already gone is swallowed.
        """
        cwd = Path(cwd)
        home = paths.grok_home()
        if grok_on_path() is not None:
            run_grok(["mcp", "remove", inj.MCPSERVER_NAME, "-s", _scope(project)],
                     home=home, cwd=cwd)
        file = grok_hooks.hook_file(home, project=project, cwd=cwd)
        try:
            removed = grok_hooks.uninstall(file)
        except OSError:
            removed = False
        if removed:
            print(f"Grok hooks removed from {file}.")

    def export_target(self, cwd: Path) -> Target:
        return target_for("grok", "auto", Path(cwd))

    def describe(self, *, project: bool, cwd: Path) -> HostStatus:
        """Grok's *own* registration; one inherited from Claude is Claude's line."""
        path = _config_path(project, Path(cwd), paths.grok_home())
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return HostStatus(name=self.name, registered=False, path=str(path), command_ok=False, detail="")
        m = _TABLE_RE.search(text)
        if not m:
            return HostStatus(name=self.name, registered=False, path=str(path), command_ok=False, detail="")
        rest = text[m.end():]
        nxt = _NEXT_TABLE_RE.search(rest)
        body = rest[: nxt.start()] if nxt else rest
        cm = _COMMAND_RE.search(body)
        if cm:
            raw = cm.group(1) if cm.group(1) is not None else cm.group(2)
            command = _toml_unescape(raw) if cm.group(1) is not None else raw
        else:
            command = ""
        ok = command_exists(command)
        detail = "" if ok else f"command not found: {command or '(missing)'}"
        return HostStatus(name=self.name, registered=True, path=str(path), command_ok=ok, detail=detail)
