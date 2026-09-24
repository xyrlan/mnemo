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
reach of the terminal's SIGHUP and SIGINT, and :func:`spawn` also orphans it,
out of reach of a kill that walks the tree from the process that started it
(#475).
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Dict, List, Optional

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def detach_kwargs() -> Dict[str, Any]:
    """Return the ``subprocess.Popen`` kwargs that detach a background worker."""
    if sys.platform == "win32":
        return {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def spawn(argv: List[str], *, cwd: Optional[str] = None) -> int:
    """Start *argv* as a detached background worker; return the worker's pid.

    Stdio goes to ``DEVNULL`` — nothing reads a detached worker's output — and
    the flags are :func:`detach_kwargs`. Raises when the worker cannot start.

    On POSIX the worker is also *orphaned* before this returns (#475): started
    by a short-lived intermediate that exits at once, so its parent is init
    rather than the hook that asked for it. A new session is not enough.
    Claude Code ends a hook still running at its ``SessionEnd`` bound (1.5 s
    unless the hook declares a ``timeout``) with a tree kill that lists every
    process by ppid and signals each descendant of the hook, whatever its
    session. A worker whose parent was still that hook got the SIGTERM too:
    three child reporters on 2026-09-23/24, each about a second after it
    started.
    """
    import subprocess

    kwargs: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    kwargs.update(detach_kwargs())
    if cwd is not None:
        kwargs["cwd"] = cwd

    def start() -> int:
        return subprocess.Popen(argv, **kwargs).pid

    if sys.platform == "win32":
        return start()
    return _orphaned(start)


def _orphaned(start: Callable[[], int]) -> int:
    """Run *start* in a forked intermediate that exits as soon as it returns.

    The worker *start* launches is left with no living parent, so init adopts
    it. Its pid comes back over a pipe, and the intermediate is reaped here,
    so it leaves no zombie. Whatever happens in the intermediate ends in
    ``os._exit``: it must never return into the caller's code and run the
    rest of the hook a second time.
    """
    import os
    import warnings

    read_fd, write_fd = os.pipe()
    with warnings.catch_warnings():
        # 3.12+ warns on fork() in a process with threads. The intermediate
        # only starts a process and exits, which is the safe use.
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:  # the intermediate
        code = 1
        try:
            os.close(read_fd)
            try:
                message = f"ok {start()}"
                code = 0
            except BaseException as exc:  # noqa: BLE001 — reported to the parent
                message = f"err {type(exc).__name__}: {exc}"
            os.write(write_fd, message.encode("utf-8", "replace"))
        finally:
            os._exit(code)
    os.close(write_fd)
    chunks = []
    try:
        while True:
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(read_fd)
        os.waitpid(pid, 0)
    reply = b"".join(chunks).decode("utf-8", "replace")
    if reply.startswith("ok "):
        return int(reply[3:])
    raise OSError(reply[4:] if reply.startswith("err ") else "detached spawn left no reply")
