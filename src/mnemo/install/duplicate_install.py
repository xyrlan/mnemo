"""Detecting a *second*, independently-versioned mnemo install.

``install.migration`` already knows that a plugin install plus a ``mnemo init``
install means every hook fires twice. What it cannot do is *notice on its own*:
its notice is emitted from the plugin's SessionStart, gated on
``CLAUDE_PLUGIN_ROOT``, and fired at most once ever (``should_notify`` writes a
marker and never speaks again). So the state stays silent in exactly the case
that hurts — when the two copies are at *different versions* and the older one
keeps running code that was fixed months ago.

This module answers the same question from the other side: given the machine's
plugin registry and settings, is a second mnemo install wired up, and is it the
same version as the copy asking? It is pure inspection with no side effects, so
``mnemo doctor`` can run it on every invocation rather than once per lifetime.

Why the registry and not the cache directory
--------------------------------------------
``~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`` **survives
uninstall**. Scanning the cache for ``hooks/hooks.json`` therefore reports a
plugin that was already removed — a false positive on any machine that ever
tried the plugin. ``installed_plugins.json`` is the registry Claude Code
actually installs from, and ``enabledPlugins`` in settings.json is what it
actually runs: a plugin needs an entry in *both* for its hooks to fire (a
registered-but-disabled plugin shows ``✘ disabled`` and fires nothing). We key
on that pair and use the cache path only to read the version that is installed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

PLUGIN_NAME = "mnemo"


@dataclass(frozen=True)
class DuplicateInstall:
    """One mnemo plugin install that overlaps the running (direct) install."""

    plugin_key: str          # e.g. "mnemo@mnemo-marketplace"
    version: str | None      # version recorded in the registry, if any
    install_path: Path | None
    enabled: bool
    hook_events: tuple[str, ...]  # events the plugin's hooks.json registers

    @property
    def runs_hooks(self) -> bool:
        """True when this install's hooks actually fire on every session."""
        return self.enabled and bool(self.hook_events)


def _read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _registered_mnemo_plugins(claude_dir: Path) -> dict[str, dict]:
    """Return ``{plugin_key: newest install record}`` for mnemo plugins.

    Reads ``plugins/installed_plugins.json`` (schema ``version: 2``), whose
    ``plugins`` map holds a *list* of install records per key — one per scope
    (user/local). We keep the first, which is the scope Claude Code resolves.
    """
    data = _read_json(claude_dir / "plugins" / "installed_plugins.json")
    if not isinstance(data, dict):
        return {}
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return {}

    found: dict[str, dict] = {}
    for key, records in plugins.items():
        if not isinstance(key, str):
            continue
        # Keys are "<plugin>@<marketplace>"; match the plugin half exactly so a
        # marketplace named "mnemo-marketplace" hosting something else, or a
        # plugin called "mnemo-helper", does not count.
        if key.split("@", 1)[0] != PLUGIN_NAME:
            continue
        if not isinstance(records, list) or not records:
            continue
        record = records[0]
        if isinstance(record, dict):
            found[key] = record
    return found


def _enabled_plugins(claude_dir: Path, cwd: Path | None = None) -> dict[str, bool]:
    """Union of the ``enabledPlugins`` maps that apply to this session.

    Enablement is not only global. The registry records ``scope`` values of
    ``user``, ``project`` and ``local``, and a project- or local-scoped plugin
    is switched on in the *project's* ``.claude/settings.json`` or
    ``settings.local.json`` — reading only ``~/.claude/settings.json`` would
    report such a plugin as disabled and understate the finding. Later sources
    win, matching how the more specific scope overrides the broader one.
    """
    if cwd is None:
        cwd = Path.cwd()
    sources = [
        claude_dir / "settings.json",
        Path(cwd) / ".claude" / "settings.json",
        Path(cwd) / ".claude" / "settings.local.json",
    ]
    merged: dict[str, bool] = {}
    for path in sources:
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        enabled = data.get("enabledPlugins")
        if not isinstance(enabled, dict):
            continue
        merged.update({k: bool(v) for k, v in enabled.items() if isinstance(k, str)})
    return merged


def _plugin_hook_events(install_path: Path | None) -> tuple[str, ...]:
    """Events registered by the plugin's own ``hooks/hooks.json``.

    The plugin declares its hooks here rather than in settings.json, which is
    why :func:`migration.find_legacy_installs` cannot see them.
    """
    if install_path is None:
        return ()
    data = _read_json(Path(install_path) / "hooks" / "hooks.json")
    if not isinstance(data, dict):
        return ()
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return ()
    return tuple(sorted(k for k in hooks if isinstance(k, str)))


def find_duplicate_installs(
    claude_dir: Path | None = None, cwd: Path | None = None,
) -> list[DuplicateInstall]:
    """Return every mnemo *plugin* install registered on this machine.

    ``claude_dir`` defaults to ``~/.claude`` and ``cwd`` to the process's
    working directory (it carries the project-scoped ``enabledPlugins``); both
    are injectable so tests — and CI, where no plugin is installed at all — can
    point at a fixture tree.

    Only the plugin side is reported: the caller is by definition the direct
    install, so a hit here means two copies. Returns ``[]`` on a machine with
    no plugin — the overwhelmingly common case — without touching the disk
    beyond two JSON reads.
    """
    if claude_dir is None:
        claude_dir = Path(os.path.expanduser("~/.claude"))
    claude_dir = Path(claude_dir)

    registered = _registered_mnemo_plugins(claude_dir)
    if not registered:
        return []

    enabled = _enabled_plugins(claude_dir, cwd)

    installs: list[DuplicateInstall] = []
    for key, record in sorted(registered.items()):
        raw_path = record.get("installPath")
        install_path = Path(raw_path) if isinstance(raw_path, str) and raw_path else None
        version = record.get("version")
        installs.append(DuplicateInstall(
            plugin_key=key,
            version=version if isinstance(version, str) else None,
            install_path=install_path,
            enabled=bool(enabled.get(key)),
            hook_events=_plugin_hook_events(install_path),
        ))
    return installs


def describe(install: DuplicateInstall, running_version: str) -> list[str]:
    """Human-readable finding lines for one duplicate install.

    Separated from the doctor adapter so the wording is testable without
    capturing stdout, and reusable by any other surface that wants to report it.
    """
    events = ", ".join(install.hook_events) if install.hook_events else "none"
    lines = [
        f"duplicate mnemo install: plugin {install.plugin_key} is registered at "
        f"version {install.version or 'unknown'} while this copy is {running_version}.",
    ]
    if install.runs_hooks:
        lines.append(
            f"    it registers its own hooks ({events}), so every session runs both "
            f"copies: capture, injection and enforcement all happen twice."
        )
    elif install.enabled:
        lines.append("    it is enabled but registers no hooks of its own.")
    else:
        lines.append("    it is installed but disabled, so its hooks do not fire today.")

    if install.version and install.version != running_version:
        lines.append(
            f"    version skew: fixes shipped after {install.version} are absent from "
            f"the copy that runs those hooks."
        )
    return lines


def version_skew(installs: list[DuplicateInstall], running_version: str) -> list[DuplicateInstall]:
    """The subset whose hooks fire at a version other than ``running_version``."""
    return [i for i in installs if i.runs_hooks and i.version != running_version]
