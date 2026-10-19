"""Doctor rows — how far this machine is from the hub (#644).

Printed only when sync is on for the vault; one that never ran ``mnemo sync
init`` or ``join`` gets no row here. Every number comes from
:func:`mnemo.core.sync.health.summary`, the same summary the statusline
reads, except the first row, which asks the hub once under
:data:`mnemo.core.sync.health.REACH_TIMEOUT` so ``doctor`` stays as fast as
#571 left it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

Summary = Dict[str, Any]
Reach = Dict[str, Any]


def _row_hub(s: Summary, reach: Reach) -> bool:
    if reach.get("ok"):
        print(f"  ℹ Sync hub: {reach.get('message')}")
        return True
    print(f"  ⚠ Sync hub: {reach.get('message')}")
    print("       → check the remote and your credentials: `mnemo sync status` names it; "
          "pulls and pushes fall back to the vault as it is")
    return False


def _row_role(s: Summary, reach: Reach) -> bool:
    print(f"  ℹ Sync role: {s['role']} (machine '{s['machineId']}', primary '{s['primary']}')")
    return True


def _row_commits(s: Summary, reach: Reach) -> bool:
    ahead, behind, pending = int(s["ahead"]), int(s["behind"]), int(s["pending"])
    files = f"{pending} file{'s' if pending != 1 else ''} waiting to push"
    if behind:
        print(f"  ⚠ Sync commits: {ahead} ahead, {behind} behind the hub as last fetched; {files}")
        print("       → `mnemo sync pull` brings the hub in; the primary's version wins a conflict")
        return False
    print(f"  ℹ Sync commits: {ahead} ahead, {behind} behind (as of the last fetch); {files}")
    return True


def _when(stamp: Any, ago: Any) -> str:
    from mnemo.core.sync.health import ago_text

    return f"{stamp} ({ago_text(ago)} ago)" if stamp else "never"


def _row_last(s: Summary, reach: Reach) -> bool:
    from mnemo.core.sync.health import ago_text

    pull = _when(s.get("lastPull"), s.get("lastPullAgoSeconds"))
    push = _when(s.get("lastPush"), s.get("lastPushAgoSeconds"))
    stale = [name for name, flag in (("pull", s.get("pullStale")), ("push", s.get("pushStale")))
             if flag]
    if not stale:
        print(f"  ℹ Sync last pull: {pull}; last push: {push}")
        return True
    limit = ago_text(int(s.get("staleAfterSeconds") or 0))
    print(f"  ⚠ Sync last pull: {pull}; last push: {push} — "
          f"no successful {' or '.join(stale)} in {limit}")
    print("       → `mnemo sync pull` then `mnemo sync push`; a machine that slept before its "
          "workers finished catches up at its next session start")
    return False


def _row_error(s: Summary, reach: Reach) -> bool:
    err = s.get("lastError")
    if not isinstance(err, dict):
        return True
    print(f"  ⚠ Sync last error: {err.get('at')} {err.get('op')}: {err.get('message')}")
    print("       → the next successful run of that command clears it")
    return False


def _row_refused(s: Summary, reach: Reach) -> bool:
    n = int(s.get("pushesRefused") or 0)
    current = [r for r in s.get("refused") or [] if isinstance(r, dict)]
    if not n and not current:
        return True
    print(f"  ⚠ Sync pushes refused by the secret gate: {n}"
          + ("; still refused:" if current else "; nothing refused now"))
    for item in current:
        print(f"       ! {item.get('path')}: {item.get('kind')}")
    if current:
        print("       → take the value out, then push again: `mnemo redact --apply` rewrites "
              "pages and briefings; a file under bots/*/memory/ is a copy of Claude Code's "
              "memory, fix the original")
    return not current


def _row_conflicts(s: Summary, reach: Reach) -> bool:
    resolved, losers = int(s.get("conflictsResolved") or 0), int(s.get("losersKept") or 0)
    if not resolved and not losers:
        return True
    print(f"  ℹ Sync conflicts: {resolved} resolved for the primary, {losers} losing "
          f"version{'s' if losers != 1 else ''} kept under {s.get('losersDir')}/")
    return True


def _row_state(s: Summary, reach: Reach) -> bool:
    trouble = s.get("stateProblem")
    if not trouble:
        return True
    print(f"  ⚠ Sync state: {trouble}; sync carries on from what .git/ records")
    print("       → the next `mnemo sync pull` or push writes it afresh; the counts and the "
          "last pull and push it held start over")
    return False


def _row_locks(s: Summary, reach: Reach) -> bool:
    from mnemo.core.sync.health import ago_text

    held = [item for item in s.get("gitLocks") or [] if isinstance(item, dict)]
    if not held:
        return True
    for item in held:
        print(f"  ⚠ Sync git lock: {item.get('path')} left {ago_text(item.get('ageSeconds'))} "
              "ago by a git that died; every pull and push stops on it")
    print("       → the next `mnemo sync pull` removes it; if it says it could not, a git "
          "process still has it open: end that process")
    return False


def _row_format(s: Summary, reach: Reach) -> bool:
    if s.get("formatSupported", True):
        return True
    print(f"  ⚠ Sync hub format: {s.get('hubFormatVersion')}; this mnemo knows "
          f"{s.get('formatVersion')} — it neither pulls nor pushes until mnemo is upgraded here")
    print("       → `pip install -U mnemo` (or the plugin update), then `mnemo sync pull`")
    return False


#: The rows, in the order they print. Each prints and returns True when silent
#: or informational, False on a warning.
ROWS: List[Tuple[str, Callable[[Summary, Reach], bool]]] = [
    ("hub", _row_hub),
    ("role", _row_role),
    ("commits", _row_commits),
    ("last", _row_last),
    ("error", _row_error),
    ("refused", _row_refused),
    ("conflicts", _row_conflicts),
    ("format", _row_format),
    ("state", _row_state),
    ("locks", _row_locks),
]


def print_rows(summary: Summary, reach: Reach) -> bool:
    ok = True
    for _name, row in ROWS:
        ok = row(summary, reach) and ok
    return ok


def _doctor_check_sync(vault: Path) -> bool:
    """Doctor-registry adapter — True when silent or informational, False on a warning."""
    from mnemo.core import config as cfg_mod
    from mnemo.core.sync import health
    from mnemo.core.sync import state as sync_state

    try:
        if not sync_state.enabled(vault):
            trouble = sync_state.problem(vault)
            if not trouble:
                return True
            print(f"  ⚠ Sync state: {trouble}, and nothing in .git/ says this vault synced; "
                  "sync reads as off here")
            print("       → if this machine synced, `mnemo sync join <remote>` again; "
                  "if it never did, delete that file")
            return False
        machine = sync_state.machine_id(cfg_mod.load_config())
        summary = health.summary(vault, machine, max_age=0)
        if summary is None:
            return True
        reach = health.reach(vault)
    except Exception as exc:  # noqa: BLE001 — a diagnostic must not abort the run
        print(f"  ⚠ Sync: could not read this machine's sync state ({exc})")
        return False
    return print_rows(summary, reach)
