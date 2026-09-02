"""Inject mnemo hooks into ~/.claude/settings.json (and the v0.5 MCP server into ~/.claude.json).

Two parallel injection flows live here:

- ``inject_hooks`` / ``uninject_hooks`` write the SessionStart + SessionEnd
  command hooks into ``~/.claude/settings.json`` (under the ``hooks`` key).
- ``inject_mcp_servers`` / ``uninject_mcp_servers`` (v0.5) write the mnemo
  MCP stdio server entry into ``~/.claude.json`` (under ``mcpServers``).
  These are *different files* — Claude Code reads hooks from settings.json
  but reads MCP servers from .claude.json at the home root.

Both flows share the same lock + backup primitives below.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from mnemo._selfexec import hook_command, self_argv, self_command
from mnemo.core import locks

# Marker substring used to identify mnemo entries in settings.json. The tag
# must be a literal substring of every valid hook command we generate so that
# uninject_hooks can find them — but it must NOT prepend or otherwise corrupt
# the command, or Claude Code will fail to dispatch the hook. The python -m
# target naturally contains "mnemo.hooks." in every command we emit, which
# makes it the perfect marker: zero collision risk and zero impact on
# executability.
#
# Kept for the installs that already exist on disk. A frozen/standalone build
# has no importable module path to name, so it invokes `mnemo hook <event>`
# instead and is matched by _BINARY_HOOK_RE below. Detection must accept both
# forms indefinitely: a binary install has to be able to clean up after a
# previous pip install, and vice versa.
MNEMO_TAG = "mnemo.hooks."


class SettingsError(Exception):
    pass


def _hook_command(module: str) -> str:
    """Return the command line that invokes a mnemo hook."""
    return hook_command(module)


HOOK_DEFINITIONS: dict[str, dict[str, Any]] = {
    "SessionStart": {
        "module": "session_start",
        "matcher": None,
        "async": False,
    },
    "UserPromptSubmit": {
        "module": "user_prompt_submit",
        "matcher": None,
        "async": False,
    },
    "PreToolUse": {
        "module": "pre_tool_use",
        "matcher": "Bash|Edit|Write|MultiEdit",
        "async": False,
    },
    "SessionEnd": {
        "module": "session_end",
        "matcher": None,
        "async": False,
    },
}


HOOK_MODULES = frozenset(d["module"] for d in HOOK_DEFINITIONS.values())

# Matches the standalone form: an executable whose basename is `mnemo`
# (optionally `.exe`, optionally quoted) invoking the `hook` subcommand with
# one of the events we actually install.
#
# Anchoring on the basename is what keeps `/home/x/mnemo-notes/bin/backup.sh`
# and `python3 .../projects/mnemo/scripts/custom.py` from being claimed as
# ours; requiring a known event keeps `mnemo status` from looking like a hook.
_BINARY_HOOK_RE = re.compile(
    r"""(?:^|[/\\\s"'])          # start, path separator, whitespace, or quote
        mnemo(?:\.exe)?          # the executable itself
        ["']?\s+hook\s+          # the subcommand
        (?:%s)\b                 # a hook event we install
    """ % "|".join(sorted(HOOK_MODULES)),
    re.VERBOSE | re.IGNORECASE,
)


def is_mnemo_hook_command(command: str) -> bool:
    """Return True when ``command`` is a hook mnemo installed.

    Accepts both the ``python -m mnemo.hooks.<event>`` form written by pip/uv
    installs and the ``mnemo hook <event>`` form a standalone build writes.
    Used by uninstall, init's idempotency check, and status's hook count, which
    previously each carried their own subtly different substring test — status
    matched a bare ``"mnemo" in command`` and so counted any unrelated command
    that happened to sit under a path containing "mnemo".
    """
    if not isinstance(command, str) or not command:
        return False
    if MNEMO_TAG in command:
        return True
    return _BINARY_HOOK_RE.search(command) is not None


def _build_entry(event: str, defn: dict[str, Any]) -> dict[str, Any]:
    hook: dict[str, Any] = {"type": "command", "command": _hook_command(defn["module"])}
    if defn.get("async"):
        hook["async"] = True
    entry: dict[str, Any] = {"hooks": [hook]}
    if defn.get("matcher"):
        entry["matcher"] = defn["matcher"]
    return entry


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SettingsError(
            f"Cannot parse {path}. mnemo refuses to overwrite a malformed settings.json. "
            f"Fix the JSON or remove the file and re-run /mnemo init. ({e})"
        )
    if not isinstance(data, dict):
        raise SettingsError(f"{path} root must be a JSON object")
    return data


def _strip_mnemo_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove every entry whose hook list is entirely mnemo commands; preserve mixed entries."""
    cleaned: list[dict[str, Any]] = []
    for entry in entries:
        hooks = entry.get("hooks", [])
        non_mnemo = [h for h in hooks if not is_mnemo_hook_command(h.get("command", ""))]
        if non_mnemo:
            new = dict(entry)
            new["hooks"] = non_mnemo
            cleaned.append(new)
        # else: drop the whole entry — it was 100% mnemo
    return cleaned


def _backup(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    backup.write_text(path.read_text())


def _with_lock(path: Path):
    return locks.try_lock(path.parent / ".mnemo-settings.lock")


def inject_hooks(settings_path: Path) -> None:
    settings_path = Path(settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 5.0
    while True:
        with _with_lock(settings_path) as held:
            if held:
                _do_inject(settings_path)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_inject(settings_path: Path) -> None:
    data = _read_settings(settings_path)
    _backup(settings_path)
    hooks = data.setdefault("hooks", {})

    # Legacy migration: strip mnemo entries from ALL hook events, not just
    # those in HOOK_DEFINITIONS. Prunes previously-installed hooks that have
    # since been removed (e.g. UserPromptSubmit, PostToolUse from v0.3.1).
    # If an event ends up with no remaining hooks after stripping, drop it.
    for event in list(hooks.keys()):
        hooks[event] = _strip_mnemo_entries(hooks[event])
        if not hooks[event]:
            del hooks[event]

    # Re-register current hooks
    for event, defn in HOOK_DEFINITIONS.items():
        existing = hooks.get(event, [])
        existing.append(_build_entry(event, defn))
        hooks[event] = existing

    settings_path.write_text(json.dumps(data, indent=2))


def uninject_hooks(settings_path: Path) -> None:
    settings_path = Path(settings_path)
    if not settings_path.exists():
        return
    deadline = time.time() + 5.0
    while True:
        with _with_lock(settings_path) as held:
            if held:
                _do_uninject(settings_path)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_uninject(settings_path: Path) -> None:
    data = _read_settings(settings_path)
    _backup(settings_path)
    hooks = data.get("hooks", {})
    for event in list(HOOK_DEFINITIONS):
        if event in hooks:
            cleaned = _strip_mnemo_entries(hooks[event])
            if cleaned:
                hooks[event] = cleaned
            else:
                hooks.pop(event)
    if not hooks:
        data.pop("hooks", None)
    settings_path.write_text(json.dumps(data, indent=2))


# --- v0.5: MCP server registration in ~/.claude.json ---


def _mcp_server_spec() -> dict[str, Any]:
    """Build the mcpServers entry for the mnemo stdio server.

    Points at whatever ran ``mnemo init`` — important when mnemo is installed
    in a venv that isn't first on PATH, and correct for a frozen build, where
    the executable is mnemo itself and takes no ``-m`` prefix.
    """
    argv = self_argv("mcp-server")
    return {"command": argv[0], "args": argv[1:]}


MCPSERVER_NAME = "mnemo"


def inject_mcp_servers(claude_json_path: Path) -> None:
    """Register the mnemo MCP server in ``~/.claude.json``. Idempotent."""
    claude_json_path = Path(claude_json_path)
    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 5.0
    while True:
        with _with_lock(claude_json_path) as held:
            if held:
                _do_inject_mcp(claude_json_path)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for .claude.json lock (5s)")
        time.sleep(0.05)


def _do_inject_mcp(claude_json_path: Path) -> None:
    data = _read_settings(claude_json_path)
    _backup(claude_json_path)
    servers = data.setdefault("mcpServers", {})
    servers[MCPSERVER_NAME] = _mcp_server_spec()
    claude_json_path.write_text(json.dumps(data, indent=2))


def uninject_mcp_servers(claude_json_path: Path) -> None:
    """Remove the mnemo MCP server entry from ``~/.claude.json``. No-op if absent."""
    claude_json_path = Path(claude_json_path)
    if not claude_json_path.exists():
        return
    deadline = time.time() + 5.0
    while True:
        with _with_lock(claude_json_path) as held:
            if held:
                _do_uninject_mcp(claude_json_path)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for .claude.json lock (5s)")
        time.sleep(0.05)


def _do_uninject_mcp(claude_json_path: Path) -> None:
    data = _read_settings(claude_json_path)
    _backup(claude_json_path)
    servers = data.get("mcpServers", {})
    servers.pop(MCPSERVER_NAME, None)
    if not servers:
        data.pop("mcpServers", None)
    claude_json_path.write_text(json.dumps(data, indent=2))


# --- v0.5: statusLine additive composer registration ---


def _statusline_compose_command() -> str:
    """Build the composer command line. Points at the running mnemo for venv correctness."""
    return self_command("statusline-compose")


def _is_mnemo_composer(spec: Any) -> bool:
    """True if the given statusLine entry is our composer (so re-init is a no-op)."""
    if not isinstance(spec, dict):
        return False
    cmd = spec.get("command", "")
    if not isinstance(cmd, str):
        return False
    return cmd.strip().endswith("statusline-compose")


def inject_statusline(settings_path: Path, vault_root: Path) -> None:
    """Install the additive statusLine composer in ``~/.claude/settings.json``.

    If the user has a pre-existing statusLine, it's preserved in
    ``<vault>/.mnemo/statusline-original.json`` and the composer wraps it.
    Re-running ``mnemo init`` is a no-op when the composer is already
    installed (the original is captured exactly once).
    """
    settings_path = Path(settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 5.0
    while True:
        with _with_lock(settings_path) as held:
            if held:
                _do_inject_statusline(settings_path, vault_root)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_inject_statusline(settings_path: Path, vault_root: Path) -> None:
    from mnemo import statusline as sl_mod

    data = _read_settings(settings_path)
    _backup(settings_path)
    existing = data.get("statusLine")

    if _is_mnemo_composer(existing):
        # Already installed — do not re-capture original (it's already saved).
        return

    # Capture original (which may be absent or anything else) into mnemo state.
    if existing is None:
        sl_mod.write_state(vault_root, None)
    elif isinstance(existing, dict):
        sl_mod.write_state(vault_root, existing)
    else:
        # Unknown shape — coerce into a string command for best-effort restore.
        sl_mod.write_state(vault_root, {"command": str(existing)})

    data["statusLine"] = {
        "type": "command",
        "command": _statusline_compose_command(),
    }
    settings_path.write_text(json.dumps(data, indent=2))


def uninject_statusline(settings_path: Path, vault_root: Path) -> None:
    """Restore the user's original statusLine and clear mnemo state."""
    settings_path = Path(settings_path)
    if not settings_path.exists():
        return
    deadline = time.time() + 5.0
    while True:
        with _with_lock(settings_path) as held:
            if held:
                _do_uninject_statusline(settings_path, vault_root)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_uninject_statusline(settings_path: Path, vault_root: Path) -> None:
    from mnemo import statusline as sl_mod

    data = _read_settings(settings_path)
    _backup(settings_path)
    current = data.get("statusLine")

    if not _is_mnemo_composer(current):
        # Not our composer — leave whatever the user has alone.
        sl_mod.clear_state(vault_root)
        return

    state = sl_mod.read_state(vault_root)
    if state and state.get("command"):
        data["statusLine"] = {
            "type": state.get("type") or "command",
            "command": state["command"],
        }
    else:
        data.pop("statusLine", None)

    sl_mod.clear_state(vault_root)
    settings_path.write_text(json.dumps(data, indent=2))


# --- v0.13: slash command registration (replaces /plugin install dance) ---
#
# Slash commands in Claude Code are filesystem-based: each command is a
# markdown file at ``~/.claude/commands/<name>.md`` (global) or
# ``<cwd>/.claude/commands/<name>.md`` (project), with optional YAML
# frontmatter and a body that uses Claude Code's bash-injection syntax
# ``!`<cmd>``` to actually run shell commands. We write one .md per
# slash command and tag each file with ``SLASH_COMMAND_TAG`` so uninject
# can identify mnemo-owned files without touching third-party commands
# that happen to share a filename.

SLASH_COMMAND_TAG = "<!-- mnemo:slash-command -->"


# Each command stores its argv, not a rendered command line: the .md files
# written at `mnemo init` must point at the mnemo that is actually installed
# rather than a bare `python3` that may resolve elsewhere. See
# _render_slash_command, and PLUGIN_COMMANDS below for the plugin's own set.
SLASH_COMMANDS: dict[str, dict[str, Any]] = {
    "init":              {"description": "first-run setup (global)",
                          "args": ("init",)},
    "init-project":      {"description": "first-run setup scoped to <cwd> (v0.12+)",
                          "args": ("init", "--project")},
    "status":            {"description": "vault state + hook health",
                          "args": ("status",)},
    "doctor":            {"description": "full diagnostic",
                          "args": ("doctor",)},
    "open":              {"description": "open vault in Obsidian",
                          "args": ("open",)},
    "fix":               {"description": "reset circuit breaker",
                          "args": ("fix",)},
    "uninstall":         {"description": "remove hooks (global; keeps vault)",
                          "args": ("uninstall",)},
    "uninstall-project": {"description": "remove hooks (project-scoped; keeps vault)",
                          "args": ("uninstall", "--project")},
    "learn":             {"description": "learn from this session now: briefing + extraction, then the rule fires on your next prompt",
                          "args": ("learn",)},
    "help":              {"description": "list commands",
                          "args": ("help",)},
}

# The subset that makes sense under the plugin. init/uninstall are absent by
# design: the plugin declares its own hooks and MCP server, so there is nothing
# for them to wire or unwire — `/plugin uninstall mnemo` is the uninstall.
# migrate-plugin is plugin-only, for users who ran `mnemo init` beforehand.
PLUGIN_COMMANDS: dict[str, dict[str, Any]] = {
    "status":  {"description": "vault state + hook health", "args": ("status",)},
    "doctor":  {"description": "full diagnostic", "args": ("doctor",)},
    "why":     {"description": "why reflex fired (or stayed silent) on your last prompts",
                "args": ("why",)},
    "learn":   {"description": "learn from this session now: briefing + extraction, then the rule fires on your next prompt",
                "args": ("learn",)},
    "open":    {"description": "open vault in Obsidian", "args": ("open",)},
    "fix":     {"description": "reset circuit breaker", "args": ("fix",)},
    "help":    {"description": "list commands", "args": ("help",)},
    "migrate": {"description": "remove a pre-plugin install so hooks stop firing twice",
                "args": ("migrate-plugin",)},
    "statusline": {"description": "install the optional mnemo status line",
                   "args": ("statusline", "--install")},
}


def render_plugin_command(spec: dict[str, Any]) -> str:
    """Render a plugin command file.

    Goes through ${CLAUDE_PLUGIN_ROOT} rather than a resolved path: the plugin
    is generated once and installed on every platform, so it cannot bake in a
    location, and the launcher is what knows where the binary actually lives.
    """
    desc = spec["description"].replace('"', '\\"')
    args = " ".join(spec["args"])
    return (
        "---\n"
        f"description: {desc}\n"
        "allowed-tools: Bash\n"
        "disable-model-invocation: true\n"
        "---\n"
        "\n"
        f'!`"${{CLAUDE_PLUGIN_ROOT}}/bin/mnemo.cmd" {args}`\n'
    )


def _render_slash_command(name: str, spec: dict[str, Any]) -> str:
    desc = spec["description"].replace('"', '\\"')
    body = (
        f"{SLASH_COMMAND_TAG}\n"
        "---\n"
        f"description: {desc}\n"
        "allowed-tools: Bash\n"
        "disable-model-invocation: true\n"
        "---\n"
        "\n"
        f"!`{self_command(*spec['args'])}`\n"
    )
    return body


def inject_slash_commands(commands_dir: Path) -> None:
    """Write mnemo slash command files into ``commands_dir``. Idempotent.

    Existing mnemo-tagged files are overwritten. Third-party files (without
    the SLASH_COMMAND_TAG marker) are left alone, even when they share a
    filename with one of mnemo's commands.
    """
    commands_dir = Path(commands_dir)
    commands_dir.mkdir(parents=True, exist_ok=True)
    for name, spec in SLASH_COMMANDS.items():
        target = commands_dir / f"{name}.md"
        # If a non-mnemo file is already at this path, leave it alone.
        if target.exists():
            try:
                if SLASH_COMMAND_TAG not in target.read_text():
                    continue
            except OSError:
                continue
        target.write_text(_render_slash_command(name, spec))


def uninject_slash_commands(commands_dir: Path) -> None:
    """Remove mnemo-tagged slash command files; preserve third-party files."""
    commands_dir = Path(commands_dir)
    if not commands_dir.exists():
        return
    for path in commands_dir.glob("*.md"):
        try:
            text = path.read_text()
        except OSError:
            continue
        if SLASH_COMMAND_TAG in text:
            try:
                path.unlink()
            except OSError:
                pass
