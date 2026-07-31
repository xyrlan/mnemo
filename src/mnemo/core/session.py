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

# Retry budget for save(): Windows os.replace can lose several races to a
# concurrent writer before winning, so a couple of retries is not enough.
_SAVE_ATTEMPTS = 5


def _cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "mnemo"


def _cache_file(session_id: str) -> Path:
    safe = _SAFE_ID.sub("_", session_id) or "unknown"
    return _cache_dir() / f"session-{safe}.json"


def save(session_id: str, info: dict[str, Any]) -> None:
    """Atomically write the session cache entry.

    Two writers can target the same session_id concurrently (the SessionEnd
    hook and ``tier3.eos-catchup``). A fixed ``<target>.tmp`` name let one
    process' ``os.replace`` consume the other's tmp file, so the loser raised
    FileNotFoundError. Each call gets its own tmp file instead. The retry
    covers two transient causes: the OS wiping $TMPDIR between mkdir and
    replace (FileNotFoundError), and Windows raising PermissionError when the
    destination is momentarily open in the racing process — POSIX has neither.
    """
    target = _cache_file(session_id)
    payload = json.dumps(info)
    last_exc: OSError | None = None
    for attempt in range(_SAVE_ATTEMPTS):
        cache_dir = _cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=cache_dir, prefix=f"{target.stem}.", suffix=".json.tmp"
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
            os.replace(tmp_name, target)
            return
        except (FileNotFoundError, PermissionError) as exc:
            last_exc = exc
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            # Stagger retries so racing writers don't collide on every attempt.
            if attempt < _SAVE_ATTEMPTS - 1:
                time.sleep(0.002 * (attempt + 1))
    if last_exc is not None:
        raise last_exc


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
    stale = [*cache_dir.glob("session-*.json"), *cache_dir.glob("session-*.json.tmp")]
    for f in stale:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


from datetime import datetime, timezone


def mark_analyzed(session_id: str) -> None:
    """Stamp ``analyzed_at`` (ISO-8601 UTC, suffix Z) into the cache file.

    No-op if the cache file does not exist or is unreadable. Atomic replace
    matches the ``save`` pattern.
    """
    info = load(session_id)
    if info is None:
        return
    info["analyzed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save(session_id, info)


def iter_unanalyzed(max_age_seconds: float = 26 * 3600) -> list[dict[str, Any]]:
    """Return cache entries with no ``analyzed_at`` and mtime within window.

    Each returned dict has ``session_id`` injected (parsed from filename).
    Malformed/unreadable files are skipped silently.
    """
    cache_dir = _cache_dir()
    if not cache_dir.exists():
        return []
    cutoff = time.time() - max_age_seconds
    out: list[dict[str, Any]] = []
    for f in cache_dir.glob("session-*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                continue
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if "analyzed_at" in data:
            continue
        # Recover session_id from filename: "session-<safe>.json"
        sid = f.stem[len("session-"):]
        data["session_id"] = sid
        out.append(data)
    return out
