"""MCP access-log writer — JSONL telemetry for tool calls."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mnemo.core.llm import LLMResponse
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


def _utc_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_llm_call(
    vault_root: Path,
    response: LLMResponse,
    *,
    purpose: str,
    model: str,
    project: str | None,
    agent: str,
    elapsed_ms: float,
) -> None:
    """Append an `llm.call` entry to mcp-access-log.jsonl. Never raises."""
    entry = {
        "timestamp": _utc_iso_z(),
        "tool": "llm.call",
        "purpose": purpose,
        "model": model,
        "project": project,
        "agent": agent,
        "usage": {
            "input_tokens": int(response.input_tokens or 0),
            "output_tokens": int(response.output_tokens or 0),
        },
        "elapsed_ms": float(elapsed_ms),
        "result_count": 1,
    }
    record(vault_root, entry)


def record_session_start_inject(
    vault_root: Path,
    *,
    envelope_bytes: int,
    included_briefing: bool,
    project: str | None,
    agent: str,
) -> None:
    """Append a `session_start.inject` entry. Never raises."""
    entry = {
        "timestamp": _utc_iso_z(),
        "tool": "session_start.inject",
        "envelope_bytes": int(envelope_bytes),
        "included_briefing": bool(included_briefing),
        "project": project,
        "agent": agent,
        "result_count": 1,
    }
    record(vault_root, entry)
