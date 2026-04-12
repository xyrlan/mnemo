# src/mnemo/core/session.py
"""Per-session IPC cache shared across hooks."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "mnemo"


def _cache_file(session_id: str) -> Path:
    safe = _SAFE_ID.sub("_", session_id) or "unknown"
    return _cache_dir() / f"session-{safe}.json"


def save(session_id: str, info: dict[str, Any]) -> None:
    _cache_dir().mkdir(parents=True, exist_ok=True)
    target = _cache_file(session_id)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(info))
    os.replace(tmp, target)


def load(session_id: str) -> dict[str, Any] | None:
    target = _cache_file(session_id)
    try:
        return json.loads(target.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, ValueError):
        try:
            target.unlink()
        except OSError:
            pass
        return None


def clear(session_id: str) -> None:
    try:
        _cache_file(session_id).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def cleanup_stale(max_age_seconds: float = 86400.0) -> None:
    cache_dir = _cache_dir()
    if not cache_dir.exists():
        return
    cutoff = time.time() - max_age_seconds
    for f in cache_dir.glob("session-*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
