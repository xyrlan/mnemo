"""MCP access-log writer — JSONL telemetry for tool calls."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.log_utils import rotate_if_needed

_LOG_FILENAME = "mcp-access-log.jsonl"
_TRUNCATE_AT = 1024


def _load_telemetry_config() -> tuple[bool, int]:
    """Return (enabled, max_bytes) from config. Never raises."""
    try:
        from mnemo.core.config import load_config
        cfg = load_config()
        tel = cfg.get("injection", {}).get("telemetry", {})
        enabled = bool(tel.get("enabled", True))
        max_bytes = int(tel.get("log", {}).get("maxBytes", 1_048_576))
        return enabled, max_bytes
    except Exception:
        return False, 1_048_576


_TRUNCATE_SUFFIX = "…[truncated]"
_TRUNCATE_KEEP = _TRUNCATE_AT - len(_TRUNCATE_SUFFIX)


def _sanitize(entry: dict) -> dict:
    """Truncate long string values in-place. Returns the same dict."""
    for key, val in entry.items():
        if isinstance(val, str) and len(val) > _TRUNCATE_AT:
            entry[key] = val[:_TRUNCATE_KEEP] + _TRUNCATE_SUFFIX
        elif isinstance(val, dict):
            _sanitize(val)
    return entry


def record(vault_root: Path, entry: dict) -> None:
    """Append one JSON line to .mnemo/mcp-access-log.jsonl. Never raises."""
    try:
        enabled, max_bytes = _load_telemetry_config()
        if not enabled:
            return

        log_dir = vault_root / ".mnemo"
        log_dir.mkdir(parents=True, exist_ok=True)

        log_path = log_dir / _LOG_FILENAME
        rotate_if_needed(log_path, max_bytes)

        line = json.dumps(_sanitize(entry)) + "\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
    except Exception:
        pass
