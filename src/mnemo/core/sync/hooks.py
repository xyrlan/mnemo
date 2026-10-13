"""What the hooks do with sync on (#643): pull before they inject, push
after they write, and never fail or wait for it.

Nothing here runs with sync off, and nothing here runs under
``MNEMO_HOOKS_OFF`` (:mod:`mnemo.core.hook_guard`), which stops sync from the
hooks as it stops everything else they do.

* :func:`pull_at_session_start` — SessionStart gives a pull at most
  :data:`PULL_BUDGET_SECONDS` of wall time and runs one at most once per
  :data:`PULL_INTERVAL_SECONDS` per vault; past the bound, offline, or inside
  the interval, the hook injects from the vault as it is. The pull runs in a
  worker process (``mnemo sync hook-pull``), so one that did not finish in
  time completes on its own. A dispatched child does not pull: its parent did.
* :func:`after_pull` — on the primary, a pull that brought briefings it had
  not seen schedules an extraction, detached, behind the same debounce and
  lock SessionEnd uses.
* :func:`push_after_write` — the briefing worker, the extraction and
  SessionStart's deferred work push what this machine wrote.

A sync that fails is an outcome in ``.mnemo/sync-state.json`` and
``.mnemo/sync-log.jsonl`` (``mnemo sync status`` shows it); only an exception
— a bug, a missing ``git`` — is logged to ``.errors.log``, under the hook's
own prefix (``session_start.sync_pull``…) so ``doctor`` groups it with the
hook's other errors. An unreachable hub is not an error: it happens on every
session start offline, and a row per start would trip the breaker.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

#: Wall seconds SessionStart waits for the pull before injecting as it is.
PULL_BUDGET_SECONDS = 3.0
#: One pull per vault per this many seconds, whatever its outcome.
PULL_INTERVAL_SECONDS = 300
#: Stamped before a pull starts, so a resume storm pulls once.
PULL_STAMP = ".mnemo/sync-hook-pull.stamp"

#: What :func:`pull_at_session_start` says it did.
OFF = "off"
HOOKS_OFF = "hooks-off"
CHILD = "child"
RECENT = "recent"
DONE = "done"
DETACHED = "detached"
ERROR = "error"


def _enabled(vault: Path) -> bool:
    from mnemo.core.hook_guard import hooks_off
    from mnemo.core.sync import state as st

    return not hooks_off() and st.enabled(Path(vault))


def _machine(cfg: Dict[str, Any], vault: Path) -> str:
    from mnemo.core.sync import roles

    return roles.machine_of(vault, cfg)


def _log(vault: Path, where: str, exc: BaseException) -> None:
    from mnemo.core import errors

    errors.log_error(Path(vault), where, exc)


# --- SessionStart ----------------------------------------------------------


def pull_is_due(vault: Path, *, now: Optional[float] = None) -> bool:
    """Whether :data:`PULL_INTERVAL_SECONDS` have passed since the last pull
    a hook started here."""
    stamp = Path(vault) / PULL_STAMP
    try:
        age = (now if now is not None else time.time()) - stamp.stat().st_mtime
    except OSError:
        return True
    return age >= PULL_INTERVAL_SECONDS or age < 0


def stamp_pull(vault: Path) -> None:
    stamp = Path(vault) / PULL_STAMP
    stamp.parent.mkdir(parents=True, exist_ok=True)
    try:
        stamp.write_bytes(b"")
        os.utime(str(stamp), None)
    except OSError:
        pass


def _start_pull_worker(vault: Path, cwd: Optional[str]):
    """Start ``mnemo sync hook-pull`` as a waitable worker. A module function
    so tests run the pull inline instead."""
    from mnemo._detach import spawn_waitable
    from mnemo._selfexec import self_argv

    return spawn_waitable(self_argv("sync", "hook-pull"),
                          cwd=cwd if cwd and os.path.isdir(cwd) else None)


def pull_at_session_start(cfg: Dict[str, Any], vault: Path, *, cwd: Optional[str],
                          budget: float = PULL_BUDGET_SECONDS) -> str:
    """Pull before the hook builds what it injects. Returns one of the words
    above. Never raises and never waits past ``budget``."""
    vault = Path(vault)
    try:
        if not _enabled(vault):
            return HOOKS_OFF if _hooks_off() else OFF
        if _is_dispatch_child(cwd):
            return CHILD
        if not pull_is_due(vault):
            return RECENT
        stamp_pull(vault)
        worker = _start_pull_worker(vault, cwd)
        if worker is None:
            return DONE
        try:
            worker.wait(timeout=budget)
        except Exception:  # noqa: BLE001 — TimeoutExpired, or a wait that failed
            return DETACHED
        return DONE
    except Exception as exc:  # noqa: BLE001 — a sync failure never fails a hook
        _log(vault, "session_start.sync_pull", exc)
        return ERROR


def _hooks_off() -> bool:
    from mnemo.core.hook_guard import hooks_off

    return hooks_off()


def _is_dispatch_child(cwd: Optional[str]) -> bool:
    try:
        from mnemo.core.dispatch import issue_for_cwd

        return issue_for_cwd(cwd) is not None
    except Exception:  # noqa: BLE001
        return False


def run_hook_pull(cfg: Dict[str, Any], vault: Path) -> Any:
    """The worker's body: the pull, then :func:`after_pull`. Returns the
    pull's outcome, ``None`` when sync is off here."""
    from mnemo.core import sync

    vault = Path(vault)
    if not _enabled(vault):
        return None
    outcome = sync.pull(vault, machine=_machine(cfg, vault), trigger="session_start")
    after_pull(cfg, vault, outcome)
    return outcome


def after_pull(cfg: Dict[str, Any], vault: Path, outcome: Any) -> bool:
    """On the primary, a pull that brought briefings schedules an extraction
    behind SessionEnd's debounce and lock. Returns whether it was asked to."""
    from mnemo.core.sync import roles

    try:
        if outcome is None or outcome.outcome != "ok":
            return False
        if not int((outcome.detail or {}).get("briefings") or 0):
            return False
        if not roles.is_primary(vault, cfg):
            return False
        from mnemo.hooks import session_end

        session_end._maybe_schedule_extraction(cfg, Path(vault), "sync")
        return True
    except Exception as exc:  # noqa: BLE001
        _log(vault, "session_start.sync_extract", exc)
        return False


# --- after writing ---------------------------------------------------------


def push_after_write(cfg: Dict[str, Any], vault: Path, *, where: str,
                     trigger: str = "session_end") -> Any:
    """Push what this machine wrote. ``where`` is the error name a failure
    that is an exception is logged under; an outcome is left to the sync
    state and log. Returns the outcome, ``None`` when sync is off here."""
    from mnemo.core import sync

    vault = Path(vault)
    try:
        if not _enabled(vault):
            return None
        from mnemo.core.config import default_config_path

        outcome = sync.push(vault, machine=_machine(cfg, vault),
                            config_path=default_config_path(), trigger=trigger)
        # A push pulls first: on the primary, what that brought counts too.
        after_pull(cfg, vault, outcome if outcome.outcome in ("ok", "nothing") else None)
        return outcome
    except Exception as exc:  # noqa: BLE001 — never fails the worker
        _log(vault, where, exc)
        return None


__all__ = [
    "CHILD", "DETACHED", "DONE", "ERROR", "HOOKS_OFF", "OFF", "PULL_BUDGET_SECONDS",
    "PULL_INTERVAL_SECONDS", "PULL_STAMP", "RECENT", "after_pull", "pull_at_session_start",
    "pull_is_due", "push_after_write", "run_hook_pull", "stamp_pull",
]
