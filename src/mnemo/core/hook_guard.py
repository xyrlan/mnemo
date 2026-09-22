"""The one env var that tells a mnemo hook it is running inside mnemo (#329).

``core.llm`` runs ``claude --print`` for briefing, extraction and ``learn``
under the user's own settings — that is deliberate, since the helper has to
reach the same subscription or key the user already authorised. What was not
deliberate is that those settings carry **mnemo's own hooks**: a helper is a
Claude Code session like any other, so it fires SessionStart and SessionEnd,
and mnemo's SessionEnd spawns more work, which spawns more helpers.

Measured on the maintainer's machine (2026-09-15): 34 ``claude --print``, 42
``mnemo.hooks.session_start`` and 42 detached ``mnemo sessions
--consume-unblocks`` processes against 7 real sessions, load average 118. Only
the circuit breaker tripping stopped it.

Verified against the real CLI (2.1.273), because the whole fix rests on two
facts that are easy to assume and cheap to check:

- ``claude --print`` does fire SessionStart and SessionEnd. A settings file
  whose hooks append to a file gets one ``start`` and one ``end`` line per
  ``--print`` run.
- the parent's environment reaches the hook process. ``MNEMO_HOOKS_OFF=1
  claude --print …`` with the same probe hooks prints ``MNEMO_HOOKS_OFF=[1]``
  from inside the hook, two processes down.

So one inherited variable is enough, and it is cheaper and more certain than
``--setting-sources``: that flag would also drop the user's model, env and
permission settings from the helper, which is a much larger change than
"mnemo does not call itself".

The variable is a *guard*, not a user-facing switch. A user who exports it in
their shell silences mnemo entirely for that session, which is why
``mnemo doctor`` says so when it sees it set.
"""
from __future__ import annotations

import os
import re
import tempfile

#: Set on every ``claude`` helper mnemo launches; read by every hook entry point.
HOOKS_OFF_ENV = "MNEMO_HOOKS_OFF"

#: Values that mean "not set" when the variable is present but empty or falsey.
_FALSEY = {"", "0", "false", "no", "off"}


def hooks_off(env: dict[str, str] | None = None) -> bool:
    """True when this process is a hook of a session mnemo launched itself.

    Anything but an unset, empty or explicitly falsey value counts as on, so
    ``MNEMO_HOOKS_OFF=1`` and ``MNEMO_HOOKS_OFF=true`` both guard. Being
    generous here is the safe direction: a misread value that guards costs one
    skipped hook, while one that does not guard costs the storm above.
    """
    source = os.environ if env is None else env
    return str(source.get(HOOKS_OFF_ENV, "")).strip().lower() not in _FALSEY


def disable_hooks(env: dict[str, str]) -> dict[str, str]:
    """Stamp *env* so the ``claude`` process it launches runs no mnemo hook."""
    env[HOOKS_OFF_ENV] = "1"
    return env


# --- throwaway sessions (#420) ---------------------------------------------
#
# Any ``claude`` session runs the global hooks, wherever it was started. A
# live test's ``tmp_path``, a background job's probe in ``$CLAUDE_JOB_DIR/tmp``
# or a scratchpad under ``/tmp/claude-<uid>/`` is a session like any other to
# Claude Code, so each one filed an agent directory, a log and a briefing in
# the user's real vault: about 90 of the 115 ``bots/`` entries on the
# maintainer's machine, three of them owning live rules.
#
# The test is on two paths, not one. A throwaway cwd is skipped only when the
# vault is *not* throwaway too: a vault that lives under the temp dir was put
# there on purpose, which is how this suite runs every hook (``tmp_path`` for
# both) and how the recorded demo runs (``init --project`` in
# ``/tmp/mnemo-demo``, the vault at ``./.mnemo``). What is refused is exactly
# the leak: a disposable directory writing into a vault that outlives it.

#: A path component pytest names its base temp dir with (``pytest-of-<user>``).
#: Matched as a component so a ``--basetemp`` outside the temp dir still counts.
_BASETEMP_RE = re.compile(r"(?:^|/)pytest-of-[^/]+(?:/|$)")


def _temp_roots() -> list[str]:
    """The system temp dir, as the platform names it and as ``/tmp``.

    ``tempfile.gettempdir()`` is ``/var/folders/…/T`` on macOS, ``/tmp`` on
    Linux and ``%TEMP%`` on Windows; ``/tmp`` is added on POSIX because macOS
    keeps Claude Code's own scratchpads (``/tmp/claude-<uid>/``) and every
    hand-made probe there, not under ``gettempdir()``.
    """
    roots = [tempfile.gettempdir()]
    if os.name == "posix":
        roots.append("/tmp")
    return roots


def _norm(path: str) -> str:
    """Resolve symlinks (``/var`` → ``/private/var``, ``/tmp`` →
    ``/private/tmp``) so a cwd Claude Code reports and a root Python reports
    compare as the same string. ``realpath`` resolves the existing prefix of a
    path that is gone, which a finished probe's cwd usually is."""
    try:
        path = os.path.realpath(path)
    except (OSError, ValueError):
        pass
    return os.path.normcase(path).replace("\\", "/").rstrip("/")


def is_throwaway(path: str, roots: list[str] | None = None) -> bool:
    """True when *path* lies under the system temp dir, a pytest base temp
    dir, or a background job's scratch dir.

    A temp dir itself counts, not only what is below it: a session started in
    ``/tmp`` is no more a project than one in ``/tmp/probe``.
    """
    if not path:
        return False
    from mnemo.core.corrections import is_job_scratch

    text = _norm(path)
    if is_job_scratch(text) or is_job_scratch(path):
        return True
    if _BASETEMP_RE.search(text):
        return True
    for root in _temp_roots() if roots is None else roots:
        base = _norm(root)
        if base and (text == base or text.startswith(base + "/")):
            return True
    return False


def throwaway_session(cwd: str, vault: str | os.PathLike) -> bool:
    """True when a hook for a session in *cwd* must leave *vault* alone.

    Every hook returns before any work when this holds — no agent directory,
    no log line, no briefing, no extraction, and no reflex or enrichment
    either: an injection into a probe is recorded in the same vault, and a
    rule surfaced there proves nothing about the project it belongs to.
    """
    return is_throwaway(str(cwd or "")) and not is_throwaway(str(vault))
