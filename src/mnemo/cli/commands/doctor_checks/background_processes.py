"""Doctor check — what the Claude Code daemon holds in memory (#280).

One line that separates the daemon's idle spare from the children it keeps
resident, because ``ps | grep bg-spare`` cannot: a claimed spare keeps its
argv, so every running child reads as a spare there. That confusion is what
turned a machine freeze into an issue about a pool — see
:mod:`mnemo.core.sessions.residents` for the measurement.

Advisory and read-only. The processes belong to Claude Code; mnemo names them
and, for a finished child, names the supported way to let it go.
"""
from __future__ import annotations

from pathlib import Path

#: Printed ids for the ``claude stop`` hint; beyond this the line says "and N more".
_MAX_IDS = 5


def _doctor_check_background_processes(
    vault: Path | None = None, *, ps_stdout: str | None = None,
    claude_home: Path | None = None, jobs_root: Path | None = None,
) -> bool:
    """Print the spare/worker split; always ``True`` (advisory).

    ``vault`` is unused on purpose, like ``background_sessions``: none of
    this lives in the vault. Tests inject the process table and both roots.
    """
    from mnemo.core.sessions import liveness, residents
    from mnemo.core.sessions.jobs import read_sessions

    table = residents.read_ps() if ps_stdout is None else ps_stdout
    if table is None:
        return True  # no `ps` here (Windows) — nothing measurable to say
    home = liveness.claude_home() if claude_home is None else claude_home
    roster = residents.read_roster_raw(home)
    if roster is None:
        # No daemon has run here — or we cannot read it, and then every
        # running child would be miscounted as an idle spare, which is the
        # exact misreading this row exists to prevent. Silence beats that.
        return True

    states = {s.short_id: s.state for s in read_sessions(jobs_root, claude_home=home)}
    found = residents.census(residents.parse_ps(table), roster, states)
    if not found.spares and not found.workers:
        print("  ✓ no background claude processes")
        return True

    spare_kb = sum(s.rss_kb for s in found.spares)
    worker_kb = sum(w.rss_kb for w in found.workers)
    by_state: dict[str, int] = {}
    for worker in found.workers:
        key = worker.state or "unknown"
        by_state[key] = by_state.get(key, 0) + 1
    states_text = ", ".join(f"{n} {state}" for state, n in sorted(by_state.items()))

    spare_word = "spare" if len(found.spares) == 1 else "spares"
    spare_text = f"{len(found.spares)} idle {spare_word}"
    if found.spares:
        oldest = max(s.age_s for s in found.spares)
        spare_text += (f" ({residents.format_mb(spare_kb)}, oldest "
                       f"{residents.format_age(oldest)})")
    worker_word = "child" if len(found.workers) == 1 else "children"
    worker_text = f"{len(found.workers)} {worker_word} resident"
    if found.workers:
        worker_text += f" ({residents.format_mb(worker_kb)}: {states_text})"

    orphaned = [s for s in found.spares if s.orphaned]
    # The daemon keeps one spare and replaces it on claim; more than one idle,
    # or one whose parent is not the running daemon, is the shape #280 feared.
    glyph = "⚠" if len(found.spares) > 1 or orphaned else "✓"
    print(f"  {glyph} claude daemon: {spare_text}; {worker_text}")
    if orphaned:
        pids = ", ".join(str(s.pid) for s in orphaned)
        print(f"    → {len(orphaned)} spare(s) not owned by the running daemon (pid {pids})")

    finished = found.finished
    if finished:
        ids = [w.short_id for w in finished if w.short_id][:_MAX_IDS]
        more = len(finished) - len(ids)
        listed = ", ".join(ids) + (f" and {more} more" if more > 0 else "")
        kb = sum(w.rss_kb for w in finished)
        print(f"  ℹ {len(finished)} finished child(ren) still hold "
              f"{residents.format_mb(kb)} until the daemon retires them (idle 8h, "
              f"or sooner on low memory); `claude stop <id>` frees one you are "
              f"done with: {listed}")
    return True
