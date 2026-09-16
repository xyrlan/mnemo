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
