"""The configuration a dispatched child starts with — and everything it drops (#270).

A child spawned by ``claude --bg`` reads the same configuration the maintainer
uses interactively: every plugin, every MCP server, every skill, every hook.
On the machine this was measured on that is nine plugins, three MCP servers
(~180 tools whose schemas cost ~97k tokens once any is loaded), ~10k tokens of
skill listings from plugins that have nothing to do with this repo, and the
``superpowers`` skills, which push a child into brainstorming a design the
issue already settled — the #236 child spent turns doing exactly that.

None of it is what the child was dispatched for, and all of it is paid on the
first turn and carried to the last.

**What this module does.** It builds the argv for a lean child: the user-level
profile is not loaded at all, and the two things dispatch actually depends on
— mnemo's hooks and mnemo's MCP server — are handed back explicitly.

What a child keeps besides those: the repo's own ``.claude/`` settings,
because a child working in a repo should obey that repo's rules, and the
repo's ``CLAUDE.md``, which is still discovered (verified: a lean child asked
for a magic word defined only in ``CLAUDE.md`` answered with it). That is the
second reason ``--bare`` was rejected — it switches CLAUDE.md auto-discovery
off, so a child would lose the standing instructions the repo wrote for it.

**Measured, not assumed** — and measured on the path dispatch actually uses.
Three ``claude --bg`` children per arm, one-word prompt in an empty git repo,
first-turn total input (``input`` + ``cache_creation`` + ``cache_read``) read
back out of each child's own transcript (claude 2.1.270, 2026-09-14):

==========================  ======================  ======================
run                         full profile (today)    lean (what this builds)
==========================  ======================  ======================
1                                           55,180                  42,293
2                                           58,285                  42,295
3                                           58,287                  42,296
==========================  ======================  ======================

**~16,000 tokens per child, ~27%**, paid on the first turn and carried to the
last. Note the spread: the lean arm varies by three tokens across runs because
it depends on nothing outside the repo, while the full arm moves with whatever
the maintainer has installed that day. Leanness buys reproducibility as well
as tokens.

A ``-p/--print`` probe of the same flags reports a much larger relative saving
(32,594 → 16,037). It is not the number quoted here: ``--print`` starts a
smaller session than ``--bg`` does to begin with, so its ratio describes a
path dispatch never takes.

**The trap this module exists to avoid.** The flags that drop the noise drop
mnemo with it — measured: with ``--strict-mcp-config`` and no ``--mcp-config``,
a child asked to list its ``mcp__*`` tools answers "NONE". mnemo installs
itself into precisely the user-level files being dropped
(``~/.claude/settings.json`` for hooks, ``~/.claude.json`` for the MCP
server), so dropping the profile without rebuilding those two leaves a child
with no vault, which is the one thing dispatch is for. Handed back, the three
``mcp__mnemo__*`` tools return and the SessionStart briefing fires (verified
by asking a live child to quote it back).

**Why these flags.** Each was checked against the installed binary rather than
read off ``--help``; the assumption is stated in
:mod:`mnemo.core.claude_cli` as ``lean-child-profile`` and exercised by
``pytest -m live_claude``.

- ``--setting-sources project,local`` — load the repo's settings, not the
  user's. This is what drops the plugins, the statusline and the unrelated
  hooks, because ``enabledPlugins`` lives in the user settings file.
- ``--strict-mcp-config`` — ignore every MCP configuration except the ones
  passed explicitly. Without it, ``~/.claude.json``'s servers load anyway and
  the largest single cost is still paid.
- ``--mcp-config <file>`` — hand mnemo's server back, copied from the live
  installation so a venv or frozen build is reproduced exactly.
- ``--settings <file>`` — hand mnemo's hooks back, filtered out of the user's
  settings by the same predicate the installer uses to recognise its own.
  All of them, not a chosen few: SessionStart is where the child reads the
  canonical project's briefing and SessionEnd is where its own is written, so
  a hand-back missing either end cuts the child out of the briefing channel
  in one direction (see :mod:`mnemo.core.dispatch`).

Rejected: ``--safe-mode`` and ``--bare``. Both are blunter than the problem —
``--safe-mode`` disables *all* customisation including mnemo's, and ``--bare``
additionally forces ``ANTHROPIC_API_KEY``/``apiKeyHelper`` auth, so a child of
an OAuth-authenticated maintainer would not start at all. Neither can express
"drop the profile but keep this one plugin's worth of it".

**Not proposed, and not done here:** touching the user's ``settings.json``, or
maintaining a copy of it. The files this writes are derived on each spawn from
whatever is installed at that moment, live under the child's own worktree, and
die with it. Nothing here is a second source of truth to keep in sync.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

#: Where the derived files live inside the child's worktree. Under the tree so
#: they are removed with it, and dot-prefixed and gitignore-worthy so a child
#: that runs `git add -A` does not commit its own launch configuration.
PROFILE_DIRNAME = ".mnemo-child-profile"

#: The flags that stop the user-level profile from loading. Stated once so the
#: live test and the argv builder cannot disagree about what "lean" means.
LEAN_FLAGS = ("--setting-sources", "project,local", "--strict-mcp-config")


def user_settings_path() -> Path:
    """``~/.claude/settings.json`` — where mnemo's hooks are installed."""
    return Path.home() / ".claude" / "settings.json"


def user_claude_json_path() -> Path:
    """``~/.claude.json`` — where mnemo's MCP server is installed.

    A different file from the hooks, and deliberately so: Claude Code reads
    hooks from ``settings.json`` and MCP servers from ``.claude.json``. Both
    have to be mined for a lean child to keep working.
    """
    return Path.home() / ".claude.json"


def _load(path: Path) -> Dict[str, Any]:
    """*path* as a JSON object, or ``{}``. Never raises.

    A missing or corrupt config is not a reason to refuse a dispatch: the
    child would simply start without the piece that could not be read, which
    is exactly what happens today when the file is absent. The caller reports
    what it could not find rather than failing the spawn.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def mnemo_hooks(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """mnemo's own hook entries, lifted out of the user's settings.

    Recognised with :func:`mnemo.install.settings.is_mnemo_hook_command`, the
    same predicate ``uninject_hooks`` and ``mnemo status`` use, so a pip
    install (``python -m mnemo.hooks.<event>``) and a frozen build
    (``mnemo hook <event>``) are both found. A hand-rolled substring test here
    would drift from the installer the first time either form changed.

    Entries are copied whole — matcher included — because ``PreToolUse`` is
    installed with one and a hook re-registered without its matcher would fire
    on every tool instead of on four.
    """
    from mnemo.install.settings import is_mnemo_hook_command

    data = _load(user_settings_path()) if settings is None else settings
    raw = data.get("hooks")
    if not isinstance(raw, dict):
        return {}

    out: Dict[str, Any] = {}
    for event, matchers in raw.items():
        if not isinstance(matchers, list):
            continue
        kept = []
        for matcher in matchers:
            if not isinstance(matcher, dict):
                continue
            ours = [
                hook for hook in matcher.get("hooks", [])
                if isinstance(hook, dict)
                and is_mnemo_hook_command(hook.get("command", ""))
            ]
            if not ours:
                continue
            entry: Dict[str, Any] = {"hooks": ours}
            if matcher.get("matcher"):
                entry["matcher"] = matcher["matcher"]
            kept.append(entry)
        if kept:
            out[event] = kept
    return out


def mnemo_mcp_servers(claude_json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The installed mnemo MCP server entry, or ``{}`` when it is not installed.

    Copied from the live installation rather than rebuilt from
    ``_mcp_server_spec()``: the installed entry points at whatever interpreter
    ran ``mnemo init``, which is the one that can import mnemo. Rebuilding it
    from the *dispatching* process would be right only while the two happen to
    be the same, and silently wrong in a venv.
    """
    from mnemo.install.settings import MCPSERVER_NAME

    data = _load(user_claude_json_path()) if claude_json is None else claude_json
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return {}
    entry = servers.get(MCPSERVER_NAME)
    return {MCPSERVER_NAME: entry} if isinstance(entry, dict) else {}


def _profile_dir(tree: Path | str) -> Path:
    directory = Path(tree) / PROFILE_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)

    # Self-ignoring, rather than a line in the repo's .gitignore: the child's
    # launch configuration is not the repo's business, and every dispatched
    # repo would otherwise need the same line added. `git add -A` in the
    # worktree — which children do run — skips the directory on this alone.
    (directory / ".gitignore").write_text("*\n", encoding="utf-8")
    return directory


def write_profile(
    tree: Path | str, settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Path]:
    """Write the derived settings and MCP config under *tree*; return their paths.

    A key is absent from the result when there was nothing to write — mnemo
    not installed, or installed without hooks — so the caller can say which
    half of the vault the child will be missing instead of pointing at an
    empty file.

    *settings* are further keys for the same settings file — a twin's
    ``autoMemoryEnabled: false`` (#453). They go into this one file rather
    than a second ``--settings``: given two, the CLI keeps the last and drops
    the first whole (measured on 2.1.280), which would take mnemo's hooks
    with it.
    """
    directory = _profile_dir(tree)
    written: Dict[str, Path] = {}

    data: Dict[str, Any] = {}
    hooks = mnemo_hooks()
    if hooks:
        data["hooks"] = hooks
    data.update(settings or {})
    if data:
        path = directory / "settings.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        written["settings"] = path

    servers = mnemo_mcp_servers()
    if servers:
        path = directory / "mcp.json"
        path.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
        written["mcp"] = path

    return written


def write_settings(tree: Path | str, settings: Mapping[str, Any]) -> Path:
    """Write *settings* alone as a child's settings file; return its path.

    For a child on the full profile, whose hooks come from the user's own
    settings and need no handing back: only the extra keys go in.
    """
    path = _profile_dir(tree) / "settings.json"
    path.write_text(json.dumps(dict(settings), indent=2), encoding="utf-8")
    return path


def lean_args(
    tree: Path | str, settings: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """The ``claude`` flags that start a lean child in *tree*.

    Order is not significant to the CLI, but is kept stable — drop, then hand
    back — so a maintainer reading a spawn in ``ps`` sees the shape of the
    decision rather than a flag soup. *settings* as in :func:`write_profile`.
    """
    written = write_profile(tree, settings)
    args = list(LEAN_FLAGS)
    if "mcp" in written:
        args += ["--mcp-config", str(written["mcp"])]
    if "settings" in written:
        args += ["--settings", str(written["settings"])]
    return args


def missing_pieces() -> List[str]:
    """What a lean child will be missing, in words. Empty when mnemo is whole.

    Reads the *installation* rather than a worktree's derived files. Absence of
    a written file is ambiguous — it also describes a tree no profile was ever
    built in — and reporting "run `mnemo init`" off that ambiguity would warn
    about a healthy install. What is actually being asked is whether mnemo is
    installed at all, so that is what is checked.
    """
    out = []
    if not mnemo_mcp_servers():
        out.append(
            "the mnemo MCP server is not registered in ~/.claude.json, so the "
            "child cannot query the vault (run `mnemo init`)"
        )
    if not mnemo_hooks():
        out.append(
            "no mnemo hooks are installed in ~/.claude/settings.json, so the "
            "child gets no briefing (run `mnemo init`)"
        )
    return out


def env_opts_out() -> bool:
    """True when ``MNEMO_DISPATCH_FULL_PROFILE`` asks for the old behaviour.

    An escape hatch that does not need a flag threaded through every caller —
    useful for a one-off dispatch of an issue that genuinely needs a plugin,
    and for bisecting a child that behaves differently lean.
    """
    value = os.environ.get("MNEMO_DISPATCH_FULL_PROFILE", "").strip().lower()
    return value not in ("", "0", "false", "no", "off")


def is_lean(requested: bool = True) -> bool:
    """Whether a child will *actually* start lean, environment included.

    The single answer to that question, because there are two ways to opt out
    — the flag and the environment variable — and anything that consults only
    one of them can disagree with what the spawn does. That is not academic:
    the report used to read ``profile: lean`` for a child that
    ``MNEMO_DISPATCH_FULL_PROFILE=1`` had just started on the full profile,
    which is the one thing the line exists to tell the truth about.
    """
    return requested and not env_opts_out()
