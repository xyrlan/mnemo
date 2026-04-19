"""Miscellaneous doctor checks that don't fit a richer concern bucket.

Hosts :func:`_doctor_check_legacy_wiki_dirs` (v0.4 fossil-directory
warning) and :func:`_doctor_check_auto_brain` (auto-extraction
heartbeat + last-run-status check).
"""
from __future__ import annotations

from pathlib import Path


def _doctor_check_legacy_wiki_dirs(vault: Path) -> bool:
    """v0.4: flag the fossil ``wiki/sources/`` and ``wiki/compiled/`` dirs.

    Extraction auto-deletes these on first v0.4 run, but users who haven't
    triggered an extract yet still see the dead dirs — warn them and tell
    them the auto-cleanup is harmless and runs next extract.
    """
    dead = [
        d for d in (vault / "wiki" / "sources", vault / "wiki" / "compiled")
        if d.exists()
    ]
    if not dead:
        return True
    # Forward slashes in user-facing output for cross-platform consistency —
    # matches the wikilink convention used everywhere else in mnemo.
    rel = ", ".join(
        str(d.relative_to(vault)).replace("\\", "/") for d in dead
    )
    print(f"  ⚠ Legacy v0.3 directories present: {rel}")
    print("       → harmless; next `mnemo extract` run will auto-delete them")
    print("         (the wiki/ hierarchy was replaced by a dashboard inside HOME.md in v0.4)")
    return False


def _doctor_check_auto_brain(vault: Path) -> bool:
    """Return True if no warnings were emitted."""
    import json as _json
    import time
    from datetime import datetime, timedelta
    from mnemo.core import config as cfg_mod

    cfg = cfg_mod.load_config()
    auto = (cfg.get("extraction", {}) or {}).get("auto", {}) or {}
    enabled = bool(auto.get("enabled", False))
    ok = True

    lock_path = vault / ".mnemo" / "extract.lock"
    if lock_path.exists():
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age > 600:
                print(f"  ⚠ Auto-brain: stale extract.lock at {lock_path} ({int(age)}s old); will auto-reclaim on next run")
                ok = False
        except OSError:
            pass

    last_run_path = vault / ".mnemo" / "last-auto-run.json"
    if not enabled:
        return ok

    if not last_run_path.exists():
        print("  ℹ Auto-brain: enabled but has never run. Hook scheduling may not be firing.")
        return ok

    try:
        payload = _json.loads(last_run_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        print("  ⚠ Auto-brain: last-auto-run.json is corrupt; delete to reset")
        return False

    exit_code = payload.get("exit_code", 0)
    error = payload.get("error") or {}
    finished_at = payload.get("finished_at")

    if exit_code != 0 and error:
        err_type = error.get("type", "error")
        err_msg = error.get("message", "")
        print(f"  ⚠ Auto-brain: FAILED on last run: {err_type}: {err_msg}")
        print(f"       → check ~/mnemo/.errors.log for extract.bg.* entries")
        ok = False

    if finished_at:
        try:
            finished_dt = datetime.fromisoformat(finished_at)
            if datetime.now() - finished_dt > timedelta(days=7):
                print(f"  ℹ Auto-brain: has not run successfully in 7+ days (last: {finished_at})")
                ok = False
        except ValueError:
            pass

    return ok
