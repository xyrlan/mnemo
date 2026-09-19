"""Per-session runtime state for mnemo (counter + injection cache + emissions).

State lives at ``<vault>/.mnemo/mcp-call-counter.json`` with shape::

    {"date": "2026-04-15", "count": 7}

The state is day-scoped: every read and every write goes through
:func:`_load`, which rolls the file over the moment it sees a date that is
not today. So the first caller of the new day — hook, statusline or MCP
tool, reader or writer — starts from an empty cache, and no later caller
can wipe what today already wrote (#374). ``read_today`` returns 0 when the
stored date is anything other than today, so a status line query never has
to know when the day rolled over.

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
    """Bump today's counter by 1, preserving the rest of the state.

    v0.8: the file stores additional runtime state (``injected_cache``,
    ``session_emissions``) alongside ``count``. A naive rewrite of
    ``{date, count}`` would silently wipe those keys on every MCP call, so
    this reads through :func:`_load` and writes the whole dict back.

    The day rollover lives in :func:`_load` and not here (#374): when it
    lived here, this call was the *only* one that noticed the new day, so
    it both let yesterday's cache keep suppressing until an MCP tool ran
    and then wiped whatever the hooks had written earlier that morning.
    """
    data = _load(vault_root)
    try:
        count = int(data.get("count", 0))
    except (TypeError, ValueError):
        count = 0  # decorative — a corrupt count restarts, it never raises
    data["count"] = count + 1
    _write(vault_root, data)


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

def _roll_over(data: dict, today: str) -> dict:
    """Reset the day-scoped state in *data* when it was written on another day.

    The whole file is scoped to one calendar day. Before #374 only
    :func:`increment` compared the stored date with today, so the rollover
    happened on the first MCP tool call rather than on the first call of any
    kind — yesterday's entries went on suppressing all morning, and that MCP
    call then discarded everything the hooks had written since midnight.

    Unknown top-level keys are left alone: they belong to a version of mnemo
    this one does not know, and guessing at their lifetime is worse than
    keeping them.
    """
    if data.get("date") == today:
        return data
    data["date"] = today
    data["count"] = 0
    data["injected_cache"] = {}
    data["session_emissions"] = {}
    return data


def _load(vault_root: Path) -> dict:
    """Load state dict with all v0.8 keys present, rolled over to today.

    Never raises.
    """
    path = _path(vault_root)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            loaded = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        loaded = {}
    loaded = _roll_over(loaded, date.today().isoformat())
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


def _session_cache(data: dict, sid: str) -> dict:
    """The mutable ``{slug: ts}`` map for *sid* inside *data*, created on demand.

    A vault written before #361 holds the flat ``{slug: ts}`` shape, whose
    entries name no session. They are dropped here rather than guessed at:
    the cache is day-scoped, so the worst case is one repeat per session
    until the rollover.
    """
    cache = data.get("injected_cache")
    if not isinstance(cache, dict):
        cache = {}
    cache = {k: v for k, v in cache.items() if isinstance(v, dict)}
    data["injected_cache"] = cache
    return cache.setdefault(sid, {})


def read_injected_cache(vault_root: Path, sid: str) -> dict:
    """Return the slugs already injected into session *sid* today (slug -> unix_ts).

    Lifetime: day-scoped, per-session. The file stores
    ``injected_cache = {sid: {slug: ts}}`` and the first access on a new
    calendar day wipes it (:func:`_roll_over`, called from :func:`_load`, so
    this read is itself enough to retire yesterday's entries — #374). Two
    concurrent sessions of the same vault do NOT share entries: a rule one
    session was told is still news to another (#361 — two thirds of the old
    vault-wide dedupe silenced a session that had never seen the rule).
    ``SessionEnd`` leaves the entries in place, so
    a ``--resume``d session, whose context still holds them, is not told
    again.

    Never raises.
    """
    cache = _load(vault_root).get("injected_cache")
    entry = cache.get(sid) if isinstance(cache, dict) else None
    return dict(entry) if isinstance(entry, dict) else {}


def add_injection(vault_root: Path, *, slug: str, sid: str, now_ts: int) -> None:
    """Record that *slug* was injected into *sid* at *now_ts* (unix seconds). Never raises."""
    data = _load(vault_root)
    _session_cache(data, sid)[slug] = int(now_ts)
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


def read_enriched_slugs(vault_root: Path, sid: str) -> set[str]:
    """Return the slugs PreToolUse enrichment already injected into *sid*. Never raises.

    Session-scoped on purpose: a vault-wide list would let the first
    dispatched child to open a file use up its notes for all its siblings
    (#271).
    """
    entry = _load(vault_root).get("session_emissions", {}).get(sid) or {}
    slugs = entry.get("enriched") or []
    return {s for s in slugs if isinstance(s, str)} if isinstance(slugs, list) else set()


def record_enrichment(vault_root: Path, *, sid: str, slugs: list[str], now_ts: int) -> None:
    """Record one enrichment emission of *slugs* for *sid* in a single write.

    Adds the slugs to the session's ``enriched`` list and to its
    ``injected_cache`` entry (so the prompt-time reflex does not repeat them
    in this session) and bumps ``enrich_count``
    once per slug. Never raises.
    """
    data = _load(vault_root)
    entry = data["session_emissions"].get(sid)
    if entry is None:
        entry = {"started_at": int(now_ts), "reflex_count": 0, "enrich_count": 0}
    enriched = entry.get("enriched") if isinstance(entry.get("enriched"), list) else []
    injected = _session_cache(data, sid)
    for slug in slugs:
        injected[slug] = int(now_ts)
        if slug not in enriched:
            enriched.append(slug)
    entry["enriched"] = enriched
    entry["enrich_count"] = int(entry.get("enrich_count", 0)) + len(slugs)
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
    """On SessionEnd: drop session_emissions[sid] entirely. Never raises.

    The session's ``injected_cache`` entry is kept until the day rollover: a
    ``--resume``d session keeps its sid and its context, so the rules it was
    told are still on screen.
    """
    data = _load(vault_root)
    if sid in data["session_emissions"]:
        del data["session_emissions"][sid]
        _write(vault_root, data)


def read_today_emissions(vault_root: Path) -> int:
    """Return today's reflex emission count (sum across sessions). Never raises.

    Used by the statusline ⚡ segment. The day rollover is invisible to
    callers: :func:`_load` empties ``session_emissions`` when the stored
    date is not today, so a stale file sums to 0 without a date check here.
    """
    data = _load(vault_root)
    total = 0
    for entry in (data.get("session_emissions") or {}).values():
        try:
            total += int(entry.get("reflex_count", 0))
        except (TypeError, ValueError):
            continue
    return total
