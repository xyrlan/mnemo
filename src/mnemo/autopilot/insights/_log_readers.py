"""Log-file readers for the autopilot insights digest.

All readers are fail-safe: missing files return empty results, malformed
lines are skipped. Filtering is always by a ``since_dt`` datetime (UTC-aware).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mnemo.core.log_utils import iter_rotated_rows


def _read_jsonl_filtered(
    path: Path,
    since_dt: datetime,
    ts_key: str,
) -> list:
    """Read a JSONL file (plus its rotated ``.1``), filter by ``ts_key >= since_dt``.

    Rotation renames the live log to ``.jsonl.1`` at 1MB, so on a busy vault
    the digest's window can straddle both files — reading only the live one
    would under-report the window.
    """
    cutoff = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []
    for entry in iter_rotated_rows(path):
        ts = entry.get(ts_key, "")
        if isinstance(ts, str) and ts >= cutoff:
            results.append(entry)
    return results


def read_mcp_access_log(vault_root: Path, since_dt: datetime) -> list:
    """Return entries from mcp-access-log.jsonl with timestamp >= since_dt."""
    path = vault_root / ".mnemo" / "mcp-access-log.jsonl"
    return _read_jsonl_filtered(path, since_dt, ts_key="timestamp")


def read_reflex_log(vault_root: Path, since_dt: datetime) -> list:
    """Return entries from reflex-log.jsonl with ts >= since_dt."""
    path = vault_root / ".mnemo" / "reflex-log.jsonl"
    return _read_jsonl_filtered(path, since_dt, ts_key="ts")


def read_denial_log(vault_root: Path, since_dt: datetime) -> list:
    """Return entries from denial-log.jsonl with timestamp >= since_dt."""
    path = vault_root / ".mnemo" / "denial-log.jsonl"
    return _read_jsonl_filtered(path, since_dt, ts_key="timestamp")


def read_recall_report(vault_root: Path) -> Optional[dict]:
    """Load recall-report.json; return None if missing or malformed."""
    path = vault_root / ".mnemo" / "recall-report.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError):
        return None
