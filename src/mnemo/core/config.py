# src/mnemo/core/config.py
"""Config loading with defaults and forward-compat preservation."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "vaultRoot": "~/mnemo",
    "capture": {
        "sessionStartEnd": True,
        "userPrompt": True,
        "fileEdits": True,
    },
    "agent": {
        "strategy": "git-root",
        "overrides": {},
    },
    "async": {
        "userPrompt": True,
        "postToolUse": True,
    },
    "extraction": {
        "model": "claude-haiku-4-5",
        "chunkSize": 10,
        "hintThreshold": 5,
        "preferAPI": False,
        "subprocessTimeout": 60,
        "costSoftCap": None,
        "auto": {
            "enabled": False,
            "minNewMemories": 5,
            "minIntervalMinutes": 60,
        },
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def default_config_path() -> Path:
    env = os.environ.get("MNEMO_CONFIG_PATH")
    if env:
        return Path(env)
    return Path(os.path.expanduser("~/mnemo/mnemo.config.json"))


def load_config(path: Path | None = None, missing_path: Path | None = None) -> dict[str, Any]:
    """Return a config dict with all defaults populated.

    `path` overrides the default lookup. `missing_path` is a no-op convenience
    used by tests to assert "no file present" without depending on $HOME.
    """
    cfg_path = path or default_config_path()
    if missing_path is not None and not missing_path.exists():
        cfg_path = missing_path
    try:
        raw = json.loads(cfg_path.read_text())
        if not isinstance(raw, dict):
            raw = {}
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
        raw = {}
    return _deep_merge(DEFAULTS, raw)


def save_config(cfg: dict[str, Any], path: Path | None = None) -> None:
    cfg_path = path or default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2))
