"""How mnemo detaches a background worker from the process that started it.

One helper for every fire-and-forget spawn, because the Windows flags are
easy to get subtly wrong and the mistake is invisible on every other OS.

On Windows the worker is started with ``CREATE_NO_WINDOW`` rather than
``DETACHED_PROCESS``. A ``DETACHED_PROCESS`` child has no console at all, and
Windows gives every console program such a process starts — ``git``, ``gh``,
``claude -p`` — a new, *visible* console window that steals focus (#452).
``CREATE_NO_WINDOW`` gives the worker a console that is never shown, and its
children inherit that console instead of opening their own. Windows ignores
``CREATE_NO_WINDOW`` when ``DETACHED_PROCESS`` is also set, so the two are
swapped, not combined.

The worker still does not share the session's console — its hidden console is
its own — and ``CREATE_NEW_PROCESS_GROUP`` stays, so a Ctrl-C in the session's
terminal never reaches it (#229: on Windows ``os.kill(pid, 0)`` *is* a Ctrl-C).

On POSIX, ``start_new_session`` puts the worker in its own session, out of
reach of the terminal's SIGHUP and SIGINT.
"""
from __future__ import annotations

import sys
from typing import Any, Dict

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def detach_kwargs() -> Dict[str, Any]:
    """Return the ``subprocess.Popen`` kwargs that detach a background worker."""
    if sys.platform == "win32":
        return {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}
