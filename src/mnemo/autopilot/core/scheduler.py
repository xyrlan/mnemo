"""Hook-driven scheduler — fires due autopilot operations.

Called from ``mnemo.hooks.session_start`` once per Claude Code session.
Replaces the previous record-only ``schedule_autopilot_job`` cron illusion.

Each operation declares a name + interval (days) + run mode (inline or
detached). The scheduler iterates them, asks ``triggers.should_run``,
and dispatches in the right mode. Everything is gated by
``kill_switch.is_active()`` so disabling autopilot stops all triggers.
"""
from __future__ import annotations

import sys
from pathlib import Path

from mnemo.autopilot.core.kill_switch import is_active
from mnemo.autopilot.core.triggers import run_detached, run_inline, should_run

# Operation registry. (name, interval_days, mode, runner-or-argv)
# - mode "inline": runner is a zero-arg callable run synchronously
# - mode "detached": runner is a list[str] argv spawned as a subprocess


def _python_for_mnemo() -> str:
    """Best-effort path to the Python interpreter that hosts mnemo."""
    return sys.executable or "python3"


def _digest_inline(vault_root: Path) -> None:
    from mnemo.autopilot.insights.digest import generate_digest, write_digest
    digest = generate_digest(vault_root=vault_root, since_days=7)
    write_digest(vault_root=vault_root, digest=digest)


def _collect_misses_inline(vault_root: Path) -> None:
    from mnemo.autopilot.insights.miss_collector import collect_recall_misses
    collect_recall_misses(vault_root=vault_root)


def run_due_jobs(*, vault_root: Path) -> dict:
    """Run any autopilot operation that is due.

    Returns a small dict reporting what fired (for logging/test introspection).
    Best-effort: every individual operation is wrapped — a failure in one
    must not block the others, and must not raise to the caller.
    """
    if not is_active(vault_root=vault_root):
        return {"active": False, "fired": []}

    fired = []
    py = _python_for_mnemo()

    # Inline (fast, <1s expected): run synchronously in the hook
    if should_run(vault_root=vault_root, name="tier0.digest", interval_days=7):
        ok = run_inline(
            vault_root=vault_root,
            name="tier0.digest",
            fn=lambda: _digest_inline(vault_root),
        )
        fired.append(("tier0.digest", "inline", ok))

    if should_run(vault_root=vault_root, name="tier0.collect-misses", interval_days=1):
        ok = run_inline(
            vault_root=vault_root,
            name="tier0.collect-misses",
            fn=lambda: _collect_misses_inline(vault_root),
        )
        fired.append(("tier0.collect-misses", "inline", ok))

    # Detached (slow, may open PRs, may call gh): spawn subprocess
    if should_run(vault_root=vault_root, name="tier1.doctor", interval_days=7):
        run_detached(
            vault_root=vault_root,
            name="tier1.doctor",
            argv=[py, "-m", "mnemo", "autopilot", "self-fix", "doctor"],
        )
        fired.append(("tier1.doctor", "detached", None))

    if should_run(vault_root=vault_root, name="tier1.sweep", interval_days=30):
        run_detached(
            vault_root=vault_root,
            name="tier1.sweep",
            argv=[py, "-m", "mnemo", "autopilot", "self-fix", "sweep"],
        )
        fired.append(("tier1.sweep", "detached", None))

    if should_run(vault_root=vault_root, name="tier1.telemetry", interval_days=7):
        run_detached(
            vault_root=vault_root,
            name="tier1.telemetry",
            argv=[py, "-m", "mnemo", "autopilot", "self-fix", "telemetry"],
        )
        fired.append(("tier1.telemetry", "detached", None))

    if should_run(vault_root=vault_root, name="tier2.bm25", interval_days=7):
        run_detached(
            vault_root=vault_root,
            name="tier2.bm25",
            argv=[py, "-m", "mnemo", "autopilot", "tune", "bm25"],
        )
        fired.append(("tier2.bm25", "detached", None))

    if should_run(vault_root=vault_root, name="tier2.reflex", interval_days=7):
        run_detached(
            vault_root=vault_root,
            name="tier2.reflex",
            argv=[py, "-m", "mnemo", "autopilot", "tune", "reflex"],
        )
        fired.append(("tier2.reflex", "detached", None))

    return {"active": True, "fired": fired}


def status_summary(*, vault_root: Path) -> list:
    """Return a list of (name, last_run_at, due) for ``mnemo autopilot status``."""
    from mnemo.autopilot.core.triggers import last_run

    operations = [
        ("tier0.digest", 7),
        ("tier0.collect-misses", 1),
        ("tier1.doctor", 7),
        ("tier1.sweep", 30),
        ("tier1.telemetry", 7),
        ("tier2.bm25", 7),
        ("tier2.reflex", 7),
    ]
    out = []
    for name, interval in operations:
        ts = last_run(vault_root=vault_root, name=name)
        due = should_run(vault_root=vault_root, name=name, interval_days=interval)
        out.append({"name": name, "interval_days": interval, "last_run_at": ts, "due": due})
    return out
