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


# --- v0.8 helpers: injected_cache + session_emissions ---

def _load(vault_root: Path) -> dict:
    """Load state dict with all v0.8 keys present. Never raises."""
    path = _path(vault_root)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            loaded = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        loaded = {}
    loaded.setdefault("date", date.today().isoformat())
    loaded.setdefault("count", 0)
    loaded.setdefault("injected_cache", {})
    loaded.setdefault("session_emissions", {})
    return loaded


def _write(vault_root: Path, data: dict) -> None:
    """Atomic write. Decorative — drops silently on OSError."""
    path = _path(vault_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def read_injected_cache(vault_root: Path) -> dict:
    """Return the injected_cache mapping (slug -> unix_ts).

    Lifetime: day-scoped, vault-wide. The cache is reset on the next day
    rollover via ``increment()`` (which also wipes ``session_emissions``).
    It is NOT scoped per-session — two concurrent sessions of the same vault
    share the cache, and ``SessionEnd`` evicts only the ``session_emissions``
    entry for the sid, not the cache slugs that sid injected.

    Never raises.
    """
    return dict(_load(vault_root).get("injected_cache", {}))


def add_injection(vault_root: Path, *, slug: str, sid: str, now_ts: int) -> None:
    """Record that *slug* was injected at *now_ts* (unix seconds). Never raises."""
    data = _load(vault_root)
    data["injected_cache"][slug] = int(now_ts)
    _write(vault_root, data)


def bump_emission(
    vault_root: Path,
    *,
    sid: str,
    kind: str,  # "reflex" | "enrich"
    now_ts: int,
) -> None:
    """Increment the emission counter for sid.kind. Seeds started_at on first bump."""
    if kind not in ("reflex", "enrich"):
        return  # silently ignore — never raise from session state
    data = _load(vault_root)
    entry = data["session_emissions"].get(sid)
    if entry is None:
        entry = {"started_at": int(now_ts), "reflex_count": 0, "enrich_count": 0}
    key = f"{kind}_count"
    entry[key] = int(entry.get(key, 0)) + 1
    data["session_emissions"][sid] = entry
    _write(vault_root, data)


def read_emission_counts(vault_root: Path, sid: str) -> dict:
    """Return {reflex_count, enrich_count} for sid; zeros if absent. Never raises."""
    entry = _load(vault_root).get("session_emissions", {}).get(sid) or {}
    return {
        "reflex_count": int(entry.get("reflex_count", 0)),
        "enrich_count": int(entry.get("enrich_count", 0)),
    }


def gc_old_sessions(vault_root: Path, *, now_ts: int, ttl_seconds: int = 24 * 3600) -> None:
    """Remove session_emissions entries whose started_at is older than ttl_seconds."""
    data = _load(vault_root)
    cutoff = int(now_ts) - int(ttl_seconds)
    survivors = {
        sid: e
        for sid, e in data.get("session_emissions", {}).items()
        if int(e.get("started_at", 0)) >= cutoff
    }
    if survivors == data.get("session_emissions"):
        return  # no-op
    data["session_emissions"] = survivors
    _write(vault_root, data)


def evict_session(vault_root: Path, sid: str) -> None:
    """On SessionEnd: drop session_emissions[sid] entirely. Never raises."""
    data = _load(vault_root)
    if sid in data["session_emissions"]:
        del data["session_emissions"][sid]
        _write(vault_root, data)


def read_today_emissions(vault_root: Path) -> int:
    """Return today's reflex emission count (sum across sessions). Never raises.

    Used by the statusline ⚡ segment. Returns 0 when the stored date is not
    today (mirroring :func:`read_today`'s behaviour) so the day rollover is
    invisible to callers.
    """
    data = _load(vault_root)
    if data.get("date") != date.today().isoformat():
        return 0
    total = 0
    for entry in (data.get("session_emissions") or {}).values():
        try:
            total += int(entry.get("reflex_count", 0))
        except (TypeError, ValueError):
            continue
    return total
