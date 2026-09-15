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
        "matcher": "Bash|Read|Edit|Write|MultiEdit",
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
    try:
        # Claude Code writes this file as UTF-8 on every platform; reading it
        # with the platform default (cp1252 on Windows) garbles non-ASCII paths.
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise SettingsError(
            f"Cannot decode {path} as UTF-8. mnemo refuses to overwrite a malformed "
            f"settings.json. Re-save the file as UTF-8 and re-run /mnemo init. ({e})"
        )
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
    # A byte copy: the backup must reproduce the file exactly, whatever it holds.
    backup.write_bytes(path.read_bytes())


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

    settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
    settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
    claude_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
    claude_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
    settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
    settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
# The one verb of the dispatch loop that belongs in the menu. It is the
# entry point: its output names the queue (`mnemo sessions`), and the README
# names the third verb (`mnemo deliver`) once, end to end. The other two stay
# CLI-only on purpose — the queue is designed never to be written into a
# session's context, and a slash command would do exactly that.
#
# `arguments` appends `$ARGUMENTS` to the rendered command line, so
# `/mnemo:dispatch 197 198` and `/mnemo:dispatch --contract plan.md` reach the
# CLI unchanged; with nothing after the name the CLI's own "nothing to
# dispatch" line explains what it wanted.
DISPATCH_COMMAND: dict[str, Any] = {
    "description": "spawn a background child per issue, or per piece of a contract "
                   "(the maintainer follows them with `mnemo sessions`, ships with `mnemo deliver`)",
    "args": ("dispatch",),
    "arguments": True,
    "argument_hint": "[issue ...] | --contract <path> [--dry-run]",
}

SLASH_COMMANDS: dict[str, dict[str, Any]] = {
    "init":              {"description": "first-run setup (global)",
                          "args": ("init",)},
    "init-project":      {"description": "first-run setup scoped to <cwd> (v0.12+)",
                          "args": ("init", "--project")},
    "status":            {"description": "vault state + hook health",
                          "args": ("status",)},
    "why":               {"description": "why the per-prompt recall fired, or stayed silent, on your last prompts",
                          "args": ("why",)},
    "doctor":            {"description": "full diagnostic",
                          "args": ("doctor",)},
    "uninstall":         {"description": "remove hooks (global; keeps vault)",
                          "args": ("uninstall",)},
    "uninstall-project": {"description": "remove hooks (project-scoped; keeps vault)",
                          "args": ("uninstall", "--project")},
    "learn":             {"description": "learn from this session now: briefing + extraction, then the rule fires on your next prompt",
                          "args": ("learn",)},
    "dispatch":          DISPATCH_COMMAND,
    "help":              {"description": "list commands",
                          "args": ("help",)},
}

# The subset that makes sense under the plugin. init/uninstall are absent by
# design: the plugin declares its own hooks and MCP server, so there is nothing
# for them to wire or unwire — `/plugin uninstall mnemo` is the uninstall.
#
# Deliberately short. A slash menu of nine made the rare commands (open, fix,
# statusline, migrate) as prominent as the daily loop, and none of them is
# something a user reaches for more than once; they stay available as CLI
# subcommands (`mnemo open`, `mnemo fix`, `mnemo statusline --install`,
# `mnemo migrate-plugin`) and `mnemo help` still lists them. `dispatch` is
# the exception (#233): it is a loop of its own, and with no entry in the
# menu the loop was unreachable from inside a session.
PLUGIN_COMMANDS: dict[str, dict[str, Any]] = {
    "status":  {"description": "vault state + hook health", "args": ("status",)},
    "why":     {"description": "why the per-prompt recall fired, or stayed silent, on your last prompts",
                "args": ("why",)},
    "doctor":  {"description": "full diagnostic", "args": ("doctor",)},
    "learn":   {"description": "learn from this session now: briefing + extraction, then the rule fires on your next prompt",
                "args": ("learn",)},
    "dispatch": DISPATCH_COMMAND,
    "help":    {"description": "list commands", "args": ("help",)},
}


def _frontmatter(spec: dict[str, Any]) -> str:
    """The YAML block Claude Code reads a command's menu entry from.

    It has to be the first bytes of the file: a line above the opening
    ``---`` — a comment, a blank — makes Claude Code treat the whole file as
    body, and the entry then shows up in the menu with that stray line as
    its description and with ``disable-model-invocation`` unread (#233).
    """
    # Both values double-quoted. Unquoted, `learn`'s "now: briefing" is a
    # nested mapping to a strict YAML parser and the hint's leading `[` a
    # sequence; either way the whole block fails to parse and the entry
    # loses its description.
    desc = spec["description"].replace('"', '\\"')
    lines = ["---", f'description: "{desc}"']
    hint = spec.get("argument_hint")
    if hint:
        lines.append(f'argument-hint: "{hint}"')
    lines += ["allowed-tools: Bash", "disable-model-invocation: true", "---"]
    return "\n".join(lines) + "\n"


def _argv(spec: dict[str, Any]) -> tuple[str, ...]:
    """The command's argv, with ``$ARGUMENTS`` appended when it takes any.

    Claude Code substitutes the placeholder with whatever followed the slash
    command before the bash line runs, so the user's own words reach the CLI
    as its arguments; with nothing after the name it expands to nothing.
    """
    args = tuple(spec["args"])
    if spec.get("arguments"):
        args += ("$ARGUMENTS",)
    return args


def render_plugin_command(spec: dict[str, Any]) -> str:
    """Render one plugin command: through the launcher, never a resolved path.

    ``${CLAUDE_PLUGIN_ROOT}`` is expanded by Claude Code at run time. The plugin
    is generated once and installed on every platform, so it cannot bake in a
    location, and the launcher is what knows where the binary actually lives.
    """
    args = " ".join(_argv(spec))
    return (
        _frontmatter(spec)
        + "\n"
        + f'!`"${{CLAUDE_PLUGIN_ROOT}}/bin/mnemo.cmd" {args}`\n'
    )


def _render_slash_command(name: str, spec: dict[str, Any]) -> str:
    return (
        _frontmatter(spec)
        + f"{SLASH_COMMAND_TAG}\n"
        + "\n"
        + f"!`{self_command(*_argv(spec))}`\n"
    )


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
                # The probe only looks for an ASCII tag; a third-party file
                # saved in another encoding is left alone, not a crash.
                if SLASH_COMMAND_TAG not in target.read_text(encoding="utf-8", errors="replace"):
                    continue
            except OSError:
                continue
        target.write_text(_render_slash_command(name, spec), encoding="utf-8")


def uninject_slash_commands(commands_dir: Path) -> None:
    """Remove mnemo-tagged slash command files; preserve third-party files."""
    commands_dir = Path(commands_dir)
    if not commands_dir.exists():
        return
    for path in commands_dir.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if SLASH_COMMAND_TAG in text:
            try:
                path.unlink()
            except OSError:
                pass


# --- skills (#233) -----------------------------------------------------------
#
# A skill is a directory holding a ``SKILL.md`` under ``~/.claude/skills/``
# (global) or ``<cwd>/.claude/skills/`` (project). The plugin ships the same
# directories at its root and Claude Code finds them by convention; an
# install that never went through the plugin has to write them itself, which
# is what ``inject_skills`` does at ``mnemo init``. The files come from the
# package (``mnemo/skills/<name>/SKILL.md``), which is also what
# ``tools/sync_plugin_manifest.py`` copies to the plugin's ``skills/``.
#
# Ownership is marked the way the slash commands are, with a tag — placed
# *after* the frontmatter, which has to stay the first bytes of the file or
# Claude Code reads the whole thing as body.

SKILL_TAG = "<!-- mnemo:skill -->"

#: Every skill the package ships, by directory name.
SKILLS: tuple[str, ...] = ("decomposing-for-dispatch",)


def read_skill(name: str) -> str:
    """The packaged ``SKILL.md`` for *name*, verbatim."""
    try:
        from importlib import resources

        root = resources.files("mnemo.skills")
    except (ImportError, AttributeError):
        # Python 3.8: ``resources.files`` does not exist, and ``read_text``
        # cannot reach a subdirectory. The package is on disk either way.
        import mnemo.skills as pkg

        root = Path(pkg.__file__).parent
    return (root / name / "SKILL.md").read_text(encoding="utf-8")


def _tag_after_frontmatter(text: str, tag: str) -> str:
    """Insert *tag* on its own line right after the closing ``---``.

    Anything above the opening ``---`` would stop Claude Code parsing the
    frontmatter; a tag at the very end would be lost the first time someone
    edits the file. Directly under the frontmatter is the one place that is
    both safe and stable. Text without frontmatter gets the tag as its first
    line, since there is then nothing to keep it under.
    """
    if text.startswith("---\n"):
        close = text.find("\n---\n", 3)
        if close != -1:
            cut = close + len("\n---\n")
            return text[:cut] + tag + "\n" + text[cut:]
    return tag + "\n" + text


def render_skill(name: str) -> str:
    """The ``SKILL.md`` ``mnemo init`` writes: the packaged text, tagged."""
    return _tag_after_frontmatter(read_skill(name), SKILL_TAG)


def inject_skills(skills_dir: Path) -> None:
    """Write every packaged skill under ``skills_dir``. Idempotent.

    A mnemo-tagged file is overwritten so an upgrade refreshes it. A
    ``SKILL.md`` without the tag belongs to someone else — a skill of the
    same name the user wrote — and is left alone, as for slash commands.
    """
    skills_dir = Path(skills_dir)
    for name in SKILLS:
        target = skills_dir / name / "SKILL.md"
        if target.exists():
            try:
                if SKILL_TAG not in target.read_text(encoding="utf-8"):
                    continue
            except OSError:
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_skill(name), encoding="utf-8")


def uninject_skills(skills_dir: Path) -> None:
    """Remove mnemo-tagged skills; leave any other ``SKILL.md`` in place.

    Only mnemo's own names are looked at — never a sweep of the directory —
    and the directory is dropped only once it holds nothing else, so a
    supporting file the user added next to the skill survives.
    """
    skills_dir = Path(skills_dir)
    for name in SKILLS:
        target = skills_dir / name / "SKILL.md"
        try:
            if not target.exists() or SKILL_TAG not in target.read_text(encoding="utf-8"):
                continue
            target.unlink()
            if not any(target.parent.iterdir()):
                target.parent.rmdir()
        except OSError:
            continue
