"""The verbs mnemo applies to a child, for a child spawned on Grok Build.

mnemo wakes a child (``mnemo resume``, the automatic rewake, pr-follow's
nudge) and stops one (``mnemo deliver``). For a Claude child each is one
``claude`` command. A Grok child has no such command: it is a session in
Grok's leader (``design/specs/2026-10-16-grok-host.md``, decision 4), so a
wake is a user turn sent through :class:`mnemo.core.grok.acp.Leader` and a
stop is ``_x.ai/session/close``.

**Which CLI a child runs on is read, never guessed.** The dispatcher records
every Grok child once, at spawn, in the ledger
:mod:`mnemo.core.sessions.child_agents` keeps: its full id, its Grok home (the
account it runs under) and its CLI. Everything here reads that line. The
environment of whoever runs the verb is not asked: a Claude child stays a
Claude child after the maintainer switches the selected account to Grok, and a
Grok child is reached under the home it was spawned in, not the current one.
A session the ledger does not name as a Grok child is a Claude child, which
keeps every Claude verb what it was before the Grok round.

**A Grok child's short id is the last eight hex digits** of its id
(:func:`mnemo.core.grok.paths.short_id`): Grok's ids are UUIDv7, and the
first eight digits of two children dispatched together are the same.

**When the leader cannot be reached** — no ``grok`` on PATH, no login, a
leader that does not come up or answer in time, an account past its limit —
the verb returns a :class:`NotReached` message. Nothing about the child
changed, and a caller that keeps a ledger about the child leaves it as it was
and tries again later rather than recording an outcome the child never saw.
A leader that answered and refused (no such session, any other error) is a
plain message: it will refuse again.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union

from mnemo.core import agent_cli
from mnemo.core.grok import paths


class NotReached(str):
    """Why a verb could not reach a Grok child's leader. A ``str``, so every
    caller that prints or records a failure reads it as one; a caller that
    keeps a ledger about the child checks for it and leaves the ledger alone.
    """


def _vault() -> Optional[Path]:
    """The vault the ledgers live in, or ``None`` when it cannot be resolved."""
    try:
        from mnemo.core import config
        from mnemo.core import paths as core_paths

        return core_paths.vault_root(config.load_config())
    except Exception:  # noqa: BLE001 — no vault reads as no Grok child
        return None


def _agents(vault_root: Union[Path, str, None]) -> Dict[str, Any]:
    """``short_id -> ChildAgent`` from the dispatch ledger. ``{}`` on any failure.

    Imported by name at call time: the ledger's module belongs to dispatch,
    and a vault with no ledger, or a mnemo without the module, has no Grok
    children to route.
    """
    root = _vault() if vault_root is None else vault_root
    if root is None:
        return {}
    try:
        module = importlib.import_module("mnemo.core.sessions.child_agents")
        found = module.read(root)
    except Exception:  # noqa: BLE001 — an unreadable ledger routes nothing
        return {}
    return found if isinstance(found, dict) else {}


def _is_grok(child: Any) -> bool:
    return (child is not None and getattr(child, "agent", None) == agent_cli.GROK
            and bool(getattr(child, "session_id", None)))


def by_session_id(session_id: Optional[str], *,
                  vault_root: Union[Path, str, None] = None) -> Optional[Any]:
    """The Grok child whose full id is *session_id*, or ``None``.

    The ledger is keyed by the short id; the full id recorded beside it must
    be *session_id* exactly, so a Claude id whose last digits happen to be a
    Grok child's key is never routed to Grok.
    """
    if not session_id:
        return None
    child = _agents(vault_root).get(paths.short_id(session_id))
    if _is_grok(child) and child.session_id == session_id:
        return child
    return None


def by_short_id(short_id: Optional[str], *,
                vault_root: Union[Path, str, None] = None) -> Optional[Any]:
    """The Grok child mnemo's ledgers key as *short_id*, or ``None``."""
    if not short_id:
        return None
    child = _agents(vault_root).get(short_id)
    return child if _is_grok(child) else None


def short_id_for(session_id: str, *, vault_root: Union[Path, str, None] = None) -> str:
    """The key mnemo's ledgers use for *session_id*: Grok's last eight, else the first eight."""
    if by_session_id(session_id, vault_root=vault_root) is not None:
        return paths.short_id(session_id)
    return (session_id or "")[:8]


def home_of(child: Any) -> Path:
    """The Grok home *child* was spawned under.

    No home recorded means none was named at spawn, which is Grok's default
    ``~/.grok`` — not whatever ``GROK_HOME`` the caller happens to have now.
    """
    home = getattr(child, "home", None)
    if home:
        return Path(home).expanduser()
    return paths.grok_home({})


def _cwd_of(child: Any, fallback: Optional[str]) -> str:
    return str(getattr(child, "cwd", None) or fallback or "")


def wake(child: Any, text: str, *, cwd: Optional[str] = None,
         timeout: float = 120.0) -> Optional[str]:
    """Send *text* to *child* as a user turn. ``None`` once the leader took it.

    The turn runs in the leader with nobody attached (the child was created
    with yolo, and a dormant one is loaded again with it). Never raises.
    """
    from mnemo.core.grok import acp

    session_id = child.session_id
    try:
        acp.Leader(home_of(child)).prompt(session_id, _cwd_of(child, cwd), text,
                                          wait=False, timeout=timeout)
    except acp.RateLimited as exc:
        return NotReached(f"Grok account at {home_of(child)} is rate limited: {exc}")
    except (acp.SessionNotFound, acp.AcpError) as exc:
        return str(exc)
    except (acp.GrokUnavailable, TimeoutError, OSError) as exc:
        return NotReached(str(exc) or type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 — one child must not take the rest
        return str(exc) or type(exc).__name__
    return None


#: Grok's answers to ``_x.ai/session/close`` after which nothing of the
#: session is left running: it closed, or it was already unloaded (dormant).
CLOSED = frozenset({"closed", "notResident"})


def close(child: Any) -> Optional[str]:
    """Close *child*'s session in its leader. ``None`` when nothing is left running.

    A dormant session answers ``notResident``: it holds no turn and no
    memory, which is what a stop is for, so that is a success too. Never
    raises.
    """
    from mnemo.core.grok import acp

    try:
        outcome = acp.Leader(home_of(child)).close(child.session_id)
    except (acp.SessionNotFound, acp.AcpError) as exc:
        return str(exc)
    except (acp.GrokUnavailable, TimeoutError, OSError) as exc:
        return NotReached(str(exc) or type(exc).__name__)
    except Exception as exc:  # noqa: BLE001
        return str(exc) or type(exc).__name__
    if outcome in CLOSED:
        return None
    return f"Grok answered {outcome!r} to closing {child.session_id}"


def _non_default_home(child: Any) -> Optional[Path]:
    home = home_of(child)
    default = paths.grok_home({})
    if os.path.normcase(os.path.realpath(str(home))) == os.path.normcase(
            os.path.realpath(str(default))):
        return None
    return home


def close_command(child: Any) -> str:
    """The command a person types to do what :func:`close` does."""
    short = paths.short_id(child.session_id)
    home = _non_default_home(child)
    return f"mnemo grok close {short}" + (f" --home {home}" if home else "")


def attach_command(child: Any) -> str:
    """The command that opens *child* in a terminal, as ``claude attach`` does.

    ``grok --cwd <tree> -r <id>`` (``-r``/``--resume`` and ``--cwd`` read from
    ``grok --help`` on 1.0.60), under the child's own home when it is not the
    default one.
    """
    tree = _cwd_of(child, None)
    if any(c.isspace() for c in tree):
        tree = f'"{tree}"'
    command = f"grok --cwd {tree} -r {child.session_id}"
    home = _non_default_home(child)
    return f"GROK_HOME={home} {command}" if home else command
