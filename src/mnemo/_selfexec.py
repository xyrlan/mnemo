"""How mnemo invokes itself.

Every self-spawn used to build ``[sys.executable, "-m", "mnemo", ...]`` inline.
That is correct for a pip/uv install, where ``sys.executable`` is a Python
interpreter, and wrong for a frozen build, where ``sys.executable`` *is* the
mnemo binary and the ``-m mnemo`` prefix becomes two stray CLI arguments.

Three shapes, because the call sites need different things:

- :func:`self_argv` — argv list for ``subprocess``; no shell involved.
- :func:`self_command` — a single shell string, for the commands written into
  settings.json, which Claude Code dispatches through bash.
- :func:`hook_command` — the settings.json command for one hook, which differs
  between the two builds by more than a prefix (see below).

And :func:`python_argv` for the opposite case: spawning a real Python
interpreter, which under a frozen build is emphatically *not* ``sys.executable``.
"""
from __future__ import annotations

import shutil
import sys

PYTHON_FALLBACK = "python3"


def is_frozen() -> bool:
    """True when running from a PyInstaller-style bundle."""
    return bool(getattr(sys, "frozen", False))


def _executable() -> str:
    return sys.executable or PYTHON_FALLBACK


def _posix(path: str) -> str:
    """Return ``path`` with forward slashes.

    Hook and statusLine commands are written into settings.json as a single
    shell string that Claude Code dispatches through bash (Git Bash on
    Windows). Bash treats ``\\`` as an escape character, so a raw Windows path
    like ``C:\\Users\\...\\python.exe`` collapses to ``C:Users...python.exe``
    and the executable becomes unreachable. Windows accepts ``/`` in paths, so
    emitting the POSIX form is safe on every platform.

    Plain string replace (not ``Path.as_posix()``) so the conversion is
    identical on every platform: on POSIX, ``Path`` treats ``\\`` as a literal
    filename character, so ``as_posix()`` would leave a Windows path's
    backslashes intact. ``sys.executable`` never legitimately contains a
    backslash on POSIX, so this is always safe.
    """
    return path.replace("\\", "/")


def self_argv(*args: str) -> list[str]:
    """Return an argv list that re-invokes mnemo with ``args``.

    Not POSIX-ified: this goes to ``subprocess`` directly, with no shell in
    between to misread a backslash.
    """
    if is_frozen():
        return [_executable(), *args]
    return [_executable(), "-m", "mnemo", *args]


def self_command(*args: str) -> str:
    """Return a shell string that re-invokes mnemo with ``args``."""
    exe = _posix(_executable())
    if is_frozen():
        return " ".join([exe, *args])
    return " ".join([exe, "-m", "mnemo", *args])


def hook_command(module: str) -> str:
    """Return the settings.json command line for hook ``module``.

    The two builds diverge more than elsewhere. A package install keeps the
    historical ``-m mnemo.hooks.<module>`` form — unchanged from what is
    already on every existing user's disk — while a frozen build has no
    importable module path and goes through the ``hook`` subcommand.
    :func:`mnemo.install.settings.is_mnemo_hook_command` recognises both.
    """
    exe = _posix(_executable())
    if is_frozen():
        return f"{exe} hook {module}"
    return f"{exe} -m mnemo.hooks.{module}"


def python_argv(*args: str) -> list[str] | None:
    """Return an argv list that runs a real Python interpreter, or None.

    For spawning Python itself rather than mnemo — the autopilot self-fix gates
    run ``-m pytest``. Under a frozen build ``sys.executable`` is the mnemo
    binary, so the usual ``[sys.executable, "-m", "pytest"]`` would be parsed
    as mnemo CLI arguments. Returns None when no interpreter can be found, so
    callers can skip the step rather than spawn something wrong.
    """
    if not is_frozen():
        return [_executable(), *args]
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return [found, *args]
    return None
