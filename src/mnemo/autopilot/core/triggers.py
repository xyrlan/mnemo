"""Hook-driven trigger registry for autopilot operations.

Replaces the original record-only ``dispatcher.schedule_autopilot_job`` cron
illusion. Instead of pretending an OS cron will run jobs, autopilot piggybacks
on existing mnemo hook events (SessionStart, SessionEnd) — every Claude Code
session naturally fires those, and we run any operation whose
``last_run_at`` is older than its interval.

Cross-platform by construction (no OS scheduler), gated by
``kill_switch.is_active()``, idempotent.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from mnemo.autopilot.core._dirs import autopilot_dir, ensure_autopilot_dir
from mnemo.autopilot.core.kill_switch import is_active

SCHEMA_VERSION = 1


def runs_path(vault_root: Path) -> Path:
    return autopilot_dir(vault_root) / "autopilot-runs.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(vault_root: Path) -> dict:
    p = runs_path(vault_root)
    if not p.exists():
        return {"schema_version": SCHEMA_VERSION, "runs": {}}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"schema_version": SCHEMA_VERSION, "runs": {}}


def _write(vault_root: Path, data: dict) -> None:
    ensure_autopilot_dir(vault_root)
    runs_path(vault_root).write_text(json.dumps(data, indent=2, sort_keys=True))


def last_run(*, vault_root: Path, name: str) -> Optional[str]:
    """Return ISO timestamp of last successful run for ``name``, or None."""
    data = _read(vault_root)
    entry = data.get("runs", {}).get(name)
    if not entry:
        return None
    return entry.get("last_run_at")


def should_run(
    *,
    vault_root: Path,
    name: str,
    interval_days: float,
) -> bool:
    """Return True iff autopilot is active AND ``name`` is due.

    Due means: never run before, or ``last_run_at`` more than ``interval_days``
    ago. Returns False when the kill switch is off/paused — callers don't
    need to gate separately.
    """
    if not is_active(vault_root=vault_root):
        return False
    ts = last_run(vault_root=vault_root, name=name)
    if ts is None:
        return True
    try:
        last = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return _now() - last >= timedelta(days=interval_days)


def mark_run(
    *,
    vault_root: Path,
    name: str,
    success: bool = True,
    error: Optional[str] = None,
) -> None:
    """Record an attempt. ``last_run_at`` only updates on success."""
    data = _read(vault_root)
    runs = data.setdefault("runs", {})
    entry = runs.setdefault(name, {})
    entry["last_attempt_at"] = _now_iso()
    if success:
        entry["last_run_at"] = _now_iso()
        entry.pop("last_error", None)
    else:
        entry["last_error"] = error or "unknown"
    _write(vault_root, data)


def run_inline(
    *,
    vault_root: Path,
    name: str,
    fn: Callable[[], None],
) -> bool:
    """Run ``fn`` synchronously, swallowing exceptions, marking the result.

    Returns True on success. Suitable only for fast operations (<1s) called
    from SessionStart — never use for grid search or PR opening.
    """
    try:
        fn()
        mark_run(vault_root=vault_root, name=name, success=True)
        return True
    except Exception as exc:
        mark_run(vault_root=vault_root, name=name, success=False, error=repr(exc)[:200])
        return False


def run_detached(
    *,
    vault_root: Path,
    name: str,
    argv: list,
) -> None:
    """Spawn ``argv`` as a detached background subprocess; mark optimistic success.

    Use for long-running operations (PR-opening self-fix, BM25 grid search,
    dead-rule sweep). We mark success **optimistically** after spawn so the
    interval gate prevents re-launching every SessionStart while the
    subprocess is still running. If the subprocess crashes, the failure
    lands in ``.mnemo/autopilot-runs.log`` and the next interval will retry.

    stdout/stderr are redirected to ``.mnemo/autopilot-runs.log`` so we can
    debug failures without keeping a terminal open.
    """
    ensure_autopilot_dir(vault_root)
    log_path = autopilot_dir(vault_root) / "autopilot-runs.log"
    try:
        with log_path.open("ab") as logf:
            logf.write(f"\n--- {_now_iso()} starting {name}: {' '.join(argv)} ---\n".encode("utf-8"))
            subprocess.Popen(
                argv,
                stdout=logf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        # mark optimistic success — interval gate prevents re-spawn loop
        mark_run(vault_root=vault_root, name=name, success=True)
    except (OSError, FileNotFoundError) as exc:
        mark_run(vault_root=vault_root, name=name, success=False, error=repr(exc)[:200])
