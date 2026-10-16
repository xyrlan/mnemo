"""Doctor check: Grok Build's side of the install, when ``grok`` is on PATH.

Reports the version, every Grok home on the machine (one per account) and
whether it is logged in, whether mnemo's hooks and MCP server are visible to
Grok in each, and whether each home's leader socket path fits the platform's
Unix-socket limit. ``grok inspect --json`` is the source of truth for what Grok
loads: it already folds in ``~/.claude/settings.json``, ``~/.claude.json``,
Grok's own hook files and ``config.toml``, and the ``[compat.claude]`` switches.

Silent when ``grok`` is not installed. No secret is read: a login is the
presence of a non-empty ``auth.json``, never its content.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from mnemo.core.grok import paths

#: Longest Unix-socket path the OS accepts, NUL excluded. macOS ``sun_path`` is
#: 104 bytes (measured: a 104-byte leader socket fails with "path must be
#: shorter than SUN_LEN"); Linux's is 108.
_SOCKET_LIMIT_DARWIN = 103
_SOCKET_LIMIT_OTHER = 107


def socket_limit(platform: Optional[str] = None) -> int:
    return _SOCKET_LIMIT_DARWIN if (platform or sys.platform) == "darwin" else _SOCKET_LIMIT_OTHER


def logged_in(home: Path) -> bool:
    """True when ``home`` holds a non-empty ``auth.json``. Its content is never read."""
    try:
        return (home / "auth.json").stat().st_size > 0
    except OSError:
        return False


def _version(home: Path, cwd: Path) -> str:
    from mnemo.hosts import grok as host

    err, out = host.run_grok(["--version"], home=home, cwd=cwd)
    return "" if err else out.strip()


def _check_home(home: Path, cwd: Path) -> bool:
    """Print the lines for one home; False when something there needs the user."""
    from mnemo.hosts import grok as host
    from mnemo.install import grok_hooks

    ok = True
    if logged_in(home):
        print(f"  ✓ Grok home {home}: logged in")
    else:
        # Not a fault of mnemo's: a home may be one the person has not signed in to yet.
        print(f"  ℹ Grok home {home}: not logged in (run `GROK_HOME={home} grok login`)")

    data = host.inspect(home, cwd)
    if data is None:
        print(f"  ✗ `grok inspect --json` failed for {home}; cannot tell what Grok loads there")
        ok = False
    else:
        hooks = host.visible_hooks(data)
        events = host.hook_events(hooks)
        missing = [e for e in grok_hooks.expected_events() if e not in events]
        if not hooks:
            print(f"  ✗ mnemo hooks are not visible to Grok in {home}")
            print("       → run `mnemo init --host grok` (a Claude install is read by Grok too: `mnemo init`)")
            ok = False
        elif missing:
            print(f"  ✗ mnemo hooks visible to Grok in {home} lack: {', '.join(missing)}")
            print("       → re-run `mnemo init --hooks-only` (or `mnemo init --host grok --hooks-only`)")
            ok = False
        else:
            where = sorted({host.source_path(h) for h in hooks if host.source_path(h)})
            print(f"  ✓ mnemo hooks visible to Grok ({', '.join(events)}) — {', '.join(where) or 'source not reported'}")
        server = host.visible_mcp(data)
        if server is None:
            print(f"  ✗ mnemo MCP server is not visible to Grok in {home}")
            print("       → run `mnemo init --host grok`")
            ok = False
        else:
            print(f"  ✓ mnemo MCP server visible to Grok — {host.source_path(server) or 'source not reported'}")

    sock = paths.leader_socket(home)
    limit = socket_limit()
    length = len(sock.encode("utf-8"))
    if length > limit:
        print(f"  ✗ leader socket {sock} is {length} bytes; this platform allows {limit}, "
              f"so Grok's leader cannot start there")
        print("       → use a shorter GROK_HOME, or set GROK_LEADER_SOCKET to a short path")
        ok = False
    else:
        print(f"  ✓ leader socket fits ({length}/{limit} bytes)")
    return ok


def _doctor_check_grok(vault: Path) -> bool:
    from mnemo.hosts import grok as host

    # `vault` is part of every doctor check's signature; Grok's wiring is not
    # vault-scoped.
    if host.grok_on_path() is None:
        return True
    cwd = Path.cwd()
    homes: List[Path] = paths.grok_homes()
    version = _version(homes[0] if homes else paths.grok_home(), cwd)
    print(f"  ✓ {version or 'grok'} on PATH")
    if not homes:
        print(f"  ℹ no Grok home yet ({paths.grok_home()}) — run `grok login` to create one")
        return True
    ok = True
    for home in homes:
        if not _check_home(home, cwd):
            ok = False
    return ok
