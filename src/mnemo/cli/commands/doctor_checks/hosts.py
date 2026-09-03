"""Doctor check: every non-Claude host with an MCP registration points at a
command that exists. A stale path (mnemo moved, venv deleted) makes the host
show a dead server with no error on mnemo's side.
"""
from __future__ import annotations

from pathlib import Path


def _doctor_check_hosts(vault: Path) -> bool:
    from mnemo.hosts import registered_hosts

    # `vault` is part of every doctor check's signature but unused here on
    # purpose: host registrations are cwd-scoped (e.g. project .cursor/mcp.json
    # lives next to the repo), not vault-scoped.
    ok = True
    for status in registered_hosts(Path.cwd()):
        if status.command_ok:
            print(f"  ✓ {status.name} MCP registered ({status.path})")
        else:
            ok = False
            print(f"  ✗ {status.name} MCP registered at {status.path} but {status.detail}")
            print(f"       → re-run `mnemo init --host {status.name}` from the environment mnemo is installed in")
    return ok
