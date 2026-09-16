"""Doctor check — mnemo's own helper processes, counted (#329).

The failure this row exists for has no other symptom a user can act on: the
machine simply goes slow. On 2026-09-15 the maintainer's went to load average
118 behind 42 detached ``mnemo sessions --consume-unblocks`` sweeps and 34
``claude --print`` helpers, started by 7 real sessions — every mnemo helper was
a Claude Code session firing mnemo's own SessionEnd, which spawned another
sweep, which ran more helpers. ``mnemo.core.hook_guard`` and the sweep lock
close that loop; this row is how a user sees it if it ever reopens, and how
they see an ordinary backlog while it drains.

Advisory and read-only: counting processes, never signalling one.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePath

#: Sweeps expected at once. The lock allows exactly one; a second is either a
#: lock that was stolen as stale or the storm shape returning.
_MAX_SWEEPS = 1

#: ``claude --print`` helpers expected at once. Several detached briefings can
#: legitimately overlap on a busy machine; a dozen cannot.
_MAX_HELPERS = 4


def _argv(command: str) -> list[str]:
    return (command or "").split()


def is_sweep(command: str) -> bool:
    """True for a ``mnemo sessions --consume-unblocks`` process.

    Matched on the flag alone: the verb is reached as ``mnemo``, as
    ``python -m mnemo`` and as a frozen executable, and the flag is the one
    token all three spellings share.
    """
    return "--consume-unblocks" in _argv(command)


def is_helper(command: str) -> bool:
    """True for a ``claude --print`` process — mnemo's helpers and any the
    user started by hand, which cost the same CPU.

    ``argv[0]`` must be the CLI itself, so a ``--print`` belonging to some
    other program is not counted. The daemon's ``bg-pty-host``/``bg-spare``
    processes are ``claude`` too but carry no print flag;
    :mod:`mnemo.core.sessions.residents` is the row that counts those.
    """
    argv = _argv(command)
    if not argv:
        return False
    exe = PurePath(argv[0]).name.lower()
    if not exe.startswith("claude"):
        return False
    return any(arg in ("--print", "-p") for arg in argv[1:])


def _doctor_check_helper_processes(
    vault: Path | None = None, *, ps_stdout: str | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    """Print the helper census; ``True`` unless something is out of shape.

    ``vault`` is unused, like the other process rows: none of this lives in
    the vault. Tests inject the process table and the environment.
    """
    from mnemo.core import hook_guard
    from mnemo.core.sessions import residents

    ok = True

    # Read before the process table: a guard left set in the user's shell is
    # the one way this check's own remedy becomes the next bug report — mnemo
    # goes completely silent and nothing says why.
    if hook_guard.hooks_off(os.environ if env is None else env):
        ok = False
        print(f"  ⚠ {hook_guard.HOOKS_OFF_ENV} is set in this environment — "
              "every mnemo hook returns immediately")
        print("    → mnemo sets it only on the `claude` helpers it launches; "
              "unset it in your shell to let mnemo see your sessions")

    table = residents.read_ps() if ps_stdout is None else ps_stdout
    if table is None:
        return ok  # no `ps` here (Windows) — nothing measurable to say

    procs = residents.parse_ps(table).values()
    sweeps = [p for p in procs if is_sweep(p.command)]
    helpers = [p for p in procs if is_helper(p.command)]

    if not sweeps and not helpers:
        print("  ✓ no mnemo helper processes running")
        return ok

    def _oldest(found: list) -> str:
        return residents.format_age(max(p.age_s for p in found))

    parts = []
    if sweeps:
        parts.append(f"{len(sweeps)} unblock sweep(s) (oldest {_oldest(sweeps)})")
    if helpers:
        parts.append(f"{len(helpers)} claude --print (oldest {_oldest(helpers)})")
    crowded = len(sweeps) > _MAX_SWEEPS or len(helpers) > _MAX_HELPERS
    print(f"  {'⚠' if crowded else 'ℹ'} mnemo helpers: {'; '.join(parts)}")
    if crowded:
        ok = False
        print("    → that is the shape of #329: one sweep at a time is the "
              "most mnemo starts. `mnemo doctor` again in a minute; if the "
              "count keeps climbing, kill them and report the versions")
    return ok
