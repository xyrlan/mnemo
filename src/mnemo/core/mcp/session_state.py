"""Per-session runtime state for mnemo (counter + injection cache + emissions).

State lives at ``<vault>/.mnemo/mcp-call-counter.json`` with shape::

    {"date": "2026-04-15", "count": 7}

When ``increment`` is called and the stored date is not today, the counter
resets to 1 (today's first call). ``read_today`` returns 0 when the stored
date is anything other than today, so a status line query never has to know
when the day rolled over.

Atomic write via tmp + os.replace so partial writes never corrupt the file.
Rare lost increments under heavy concurrency are acceptable — this counter
is decorative, not accounting.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

_FILENAME = "mcp-call-counter.json"


def _path(vault_root: Path) -> Path:
    return vault_root / ".mnemo" / _FILENAME


def increment(vault_root: Path) -> None:
    """Bump today's counter by 1, preserving unknown top-level keys.

    v0.8: the file now stores additional runtime state (``injected_cache``,
    ``session_emissions``) alongside ``count``. A naive rewrite of
    ``{date, count}`` would silently wipe those keys on every MCP call.
    """
    path = _path(vault_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return  # decorative — never block the caller
    today = date.today().isoformat()
    data: dict = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        data = {}
    if data.get("date") != today:
        # Day rollover wipes count AND runtime state.
        data = {
            "date": today,
            "count": 0,
            "injected_cache": {},
            "session_emissions": {},
        }
    data["count"] = int(data.get("count", 0)) + 1
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def read_today(vault_root: Path) -> int:
    """Return today's call count, or 0 if the file is missing/stale/corrupt."""
    path = _path(vault_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    if data.get("date") != date.today().isoformat():
        return 0
    try:
        return int(data.get("count", 0))
    except (TypeError, ValueError):
        return 0
