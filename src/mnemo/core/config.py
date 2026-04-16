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
    },
    "agent": {
        "strategy": "git-root",
        "overrides": {},
    },
    "extraction": {
        "model": "claude-haiku-4-5",
        "chunkSize": 10,
        "preferAPI": False,
        "subprocessTimeout": 60,
        "costSoftCap": None,
        "auto": {
            "enabled": True,
            "minNewMemories": 1,
            "minIntervalMinutes": 60,
        },
    },
    "briefings": {
        "enabled": True,
    },
    "injection": {
        "enabled": True,
        "telemetry": {
            "enabled": True,
            "log": {"maxBytes": 1_048_576},
        },
    },
    "enforcement": {
        # v0.5: enabled by default. The PreToolUse hook is fail-open at every
        # stage and only acts on rules that survive the consumer-visible gate
        # in build_index, so the worst-case impact of a misconfigured rule is
        # an unblocked tool call — never a broken Claude Code session.
        "enabled": True,
        "log": {"maxBytes": 1_048_576},
    },
    "enrichment": {
        "enabled": True,
        "maxRulesPerCall": 3,
        "bodyPreviewChars": 300,
        "log": {"maxBytes": 1_048_576},
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
