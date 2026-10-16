"""Which agent CLI a mnemo process drives: Claude Code or Grok Build.

mnemo was built against Claude Code alone. Since the Grok round
(``design/specs/2026-10-16-grok-host.md``) a second CLI, xAI's ``grok``, can
host sessions and run dispatched children. Everything that spawns, wakes,
stops or reads a session asks this module which one is meant, and nothing
else decides it.

The signal comes from the maintainer's account choice. mnemo-desktop exports
``MNEMO_AGENT`` into every pane and job it starts, from the kind of the
account selected in its accounts tab; a command line can say ``--agent``.
Inside a Grok session the shell carries ``GROK_SESSION_ID`` (measured on
grok 1.0.60, 2026-10-16), so a ``mnemo`` a Grok session runs knows where it is
even when nobody exported anything. ``GROK_SESSION_ID`` wins over the Claude
variables because a ``grok`` started from inside a Claude Code session
inherits ``CLAUDE_CODE_SESSION_ID`` as well (seen in the same probe).

The answer for one *session* is recorded where the session is (its grant,
its ``Session.agent``), never re-derived from the environment of whoever
reads it later: a Claude child stays a Claude child after the maintainer
switches the active account to Grok.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional, Tuple

CLAUDE = "claude"
GROK = "grok"

#: Every CLI mnemo can drive. Order is the order ``--agent`` lists them.
AGENT_CLIS: Tuple[str, ...] = (CLAUDE, GROK)

#: The variable mnemo-desktop exports from the selected account's kind.
ENV_VAR = "MNEMO_AGENT"

#: Set by Grok Build in the environment of every command its shell tool runs.
GROK_SESSION_ENV = "GROK_SESSION_ID"


class UnknownAgentError(ValueError):
    """``MNEMO_AGENT`` (or ``--agent``) names a CLI mnemo cannot drive."""


def validate(name: str) -> str:
    """``name`` if it is one of :data:`AGENT_CLIS`, else :class:`UnknownAgentError`."""
    if name in AGENT_CLIS:
        return name
    raise UnknownAgentError(
        f"unknown agent CLI {name!r}; known: {', '.join(AGENT_CLIS)}"
    )


def current_cli(
    env: Optional[Mapping[str, str]] = None, *, override: Optional[str] = None,
) -> str:
    """The agent CLI this process should drive.

    In order: ``override`` (a ``--agent`` flag), then ``MNEMO_AGENT``, then
    ``grok`` when ``GROK_SESSION_ID`` is set, then ``claude``. An empty value
    counts as unset; an unknown one raises rather than falling back, so a
    typo never silently runs children on the other CLI.
    """
    if override:
        return validate(override)
    env = os.environ if env is None else env
    named = (env.get(ENV_VAR) or "").strip()
    if named:
        return validate(named)
    if (env.get(GROK_SESSION_ENV) or "").strip():
        return GROK
    return CLAUDE
