"""Inject mnemo hooks into ~/.claude/settings.json."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from mnemo.core import locks

MNEMO_TAG = "mnemo:"  # marker substring in command field


class SettingsError(Exception):
    pass


def _hook_command(module: str) -> str:
    """Return the command line that invokes a mnemo hook."""
    return f"{MNEMO_TAG} {sys.executable or 'python3'} -m mnemo.hooks.{module}"


HOOK_DEFINITIONS: dict[str, dict[str, Any]] = {
    "SessionStart": {
        "module": "session_start",
        "matcher": None,
        "async": False,
    },
    "SessionEnd": {
        "module": "session_end",
        "matcher": None,
        "async": False,
    },
    "UserPromptSubmit": {
        "module": "user_prompt",
        "matcher": None,
        "async": True,
    },
    "PostToolUse": {
        "module": "post_tool_use",
        "matcher": "Write|Edit",
        "async": True,
    },
}


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
        non_mnemo = [h for h in hooks if MNEMO_TAG not in h.get("command", "")]
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
    for event, defn in HOOK_DEFINITIONS.items():
        existing = hooks.get(event, [])
        existing = _strip_mnemo_entries(existing)
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
