"""Grok children as :class:`~mnemo.core.sessions.jobs.Session` records (grok-host wave 2).

A Grok child has no ``state.json``. What a Claude job record says in one file
is spread over two sources here, and this module is the only one that knows
either (``design/specs/2026-10-16-grok-host.md``):

- **Which sessions are children** comes from grok-spawn's ledger,
  :func:`mnemo.core.sessions.child_agents.read`: the full id, the home and the
  cwd, recorded once at spawn. Nothing is re-derived; a Grok session nobody
  dispatched is not a child and is not listed, the way an interactive
  ``claude`` is not.
- **What it is doing** comes from the leader's roster
  (:meth:`mnemo.core.grok.acp.Leader.roster`), the only live source, and from
  the session's own files. Measured on grok 1.0.60 (2026-10-16) over 18
  sessions in one home (``tools/measure_grok_stops.py`` counts them):

  - ``events.jsonl`` logs ``turn_started``, ``permission_requested``,
    ``permission_resolved`` and ``turn_ended{outcome}``; ``outcome`` was
    ``completed`` (11) or ``error`` (6).
  - Every one of the six ``error`` turns was a rate limit, and only
    ``updates.jsonl`` says so: ``turn_completed`` with ``stop_reason:
    "rate_limit"``, after a ``retry_state`` of type ``exhausted`` carrying
    the API's sentence (:mod:`mnemo.core.sessions.stalls` reads it).
  - ``summary.json`` carries ``generated_title``, ``last_turn_summary``,
    ``current_model_id``, ``reasoning_effort`` and ``updated_at``.

**How the fields map.** ``state`` and ``tempo`` keep the meanings
:mod:`mnemo.core.sessions.jobs` gives them:

==============================  ===========  ========  ==========================
what the evidence says          ``state``    ``tempo``  ``needs``
==============================  ===========  ========  ==========================
roster ``working``              working      active
roster ``needs_input``, or a    blocked      blocked   the tool waiting on approval
permission request still open
last turn ended ``completed``   done         idle
last turn hit a rate limit      blocked      blocked   Claude's own sentence for it
last turn ended another error   blocked      blocked   ``API error — see detail``
a turn the log never ended, in  stopped      idle
a session not running any more
==============================  ===========  ========  ==========================

``live`` is the roster's ``resident``: the session is held by a running
leader, the way a Claude worker is held by its daemon. A readable roster that
does not list the session, or a leader that is provably not listening (a
missing socket, a refused connection), is ``False``; a roster that could not
be read is ``None``, and a session with ``None`` is treated as live, as
everywhere else. A dispatched child has no client attached, so the leader
unloads it as ``dormant`` when its turn ends (spec: "A session left idle when
its last client disconnects is unloaded as dormant"); a child stopped by a
rate limit therefore reads blocked and not live, which is what files it under
STALLED and lets ``mnemo resume`` wake it, as a Claude child's dead worker
does.

``model`` and ``effort`` are the roster's, else ``summary.json``'s. Grok
writes the effort it runs at even when none was asked for, so ``effort`` is
rarely ``None`` here, and that is the truth about the session.
``context_tokens`` is filled by :func:`mnemo.core.activity.context.grok_context`;
``context_breakdown``, ``tokens``, ``intent``, ``suggested_reply`` and
``children`` have no Grok equivalent and stay ``None`` or empty.

**What it costs.** Nothing at all on a machine with no Grok child. Otherwise
one roster per home that holds a child whose worktree still exists, and only
when that home's leader is listening: a reader never starts a leader. A
roster measured 0.15 s against a running leader (0.6 s when the bridge had to
start one, which this module never asks it to). It is bounded by
:data:`ROSTER_TIMEOUT` because the statusline reads sessions on every render.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from mnemo.core.sessions.jobs import Session

AGENT = "grok"

#: Seconds a roster may take before this home's sessions read ``live=None``.
#: Unknown is the safe direction (never "dead"), and the statusline composer
#: gives the whole status line 2 s.
ROSTER_TIMEOUT = 1.5

#: Seconds the socket probe may take. A listening leader accepts at once.
PROBE_TIMEOUT = 0.5

#: Bytes of ``events.jsonl`` read from its end. ``phase_changed`` dominates the
#: file (675 of 813 events measured) and a turn's own events sit among them;
#: the event that decides the phase is within the last few hundred lines.
EVENTS_WINDOW = 65536

#: Claude Code's ``needs`` sentence for a rate limit, so the queue says the
#: same words for a Grok child and :func:`stalls.from_needs` reads them too.
RATE_LIMITED_NEEDS = "rate limited — wait and retry"
API_ERROR_NEEDS = "API error — see detail"

EVENTS = "events.jsonl"
SUMMARY = "summary.json"
SIGNALS = "signals.json"


# --------------------------------------------------------------------------
# Who the children are


def _default_vault() -> Optional[Path]:
    from mnemo.core.sessions import accounts

    return accounts._default_vault()


def children(vault_root: Union[Path, str, None] = None) -> List[Any]:
    """Every Grok child grok-spawn recorded, oldest ledger order kept. Never raises."""
    try:
        vault = _default_vault() if vault_root is None else Path(vault_root)
        if vault is None:
            return []
        from mnemo.core.sessions import child_agents

        found = child_agents.read(vault)
    except Exception:  # noqa: BLE001 — a ledger that cannot be read costs the Grok rows only
        return []
    values: Iterable[Any] = found.values() if isinstance(found, Mapping) else ()
    return [c for c in values
            if getattr(c, "agent", None) == AGENT and getattr(c, "session_id", None)]


def home_of(child: Any) -> Path:
    """The Grok home a child runs under: the recorded one, else ``~/.grok``.

    Not ``$GROK_HOME`` of this process: the child ran under its dispatcher's
    home, which the ledger recorded, and ``None`` there means the dispatcher
    had none set.
    """
    home = getattr(child, "home", None)
    return Path(home).expanduser() if home else Path.home() / ".grok"


# --------------------------------------------------------------------------
# The leader


def leader_listening(home: Path) -> Optional[bool]:
    """Whether ``home``'s leader accepts connections. ``None`` when it cannot be told.

    A connect, never a spawn: ``grok agent --leader stdio`` would start a
    leader to answer, and listing sessions must not start anything. A socket
    file that refuses (a leader that died and left it) or no file at all is a
    leader that is down, and a down leader holds no session.
    """
    from mnemo.core.grok import acp

    try:
        path = acp.leader_socket_for(home)
    except Exception:  # noqa: BLE001
        return None
    family = getattr(socket, "AF_UNIX", None)
    if family is None:
        return None  # no Unix sockets in this Python: nothing to probe with
    if not os.path.exists(path):
        return False
    probe = socket.socket(family, socket.SOCK_STREAM)
    probe.settimeout(PROBE_TIMEOUT)
    try:
        probe.connect(path)
    except (ConnectionRefusedError, FileNotFoundError):
        return False
    except OSError:
        return None
    finally:
        probe.close()
    return True


def _fetch_roster(home: Path) -> List[Any]:
    """``Leader(home).roster()``. Its own function so tests replace it."""
    from mnemo.core.grok import acp

    return acp.Leader(home).roster()


def read_roster(home: Path, *, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """``session id -> RosterEntry`` for ``home``, ``{}`` when no leader runs, ``None`` when unknown.

    The same tri-state as :func:`mnemo.core.sessions.liveness.read_roster`:
    ``{}`` proves nothing is resident, ``None`` proves nothing.
    """
    if not home.is_dir():
        return {}  # a home that is gone runs no leader
    listening = leader_listening(home)
    if listening is False:
        return {}
    if listening is None:
        return None

    box: Dict[str, Any] = {}

    def run() -> None:
        try:
            box["rows"] = _fetch_roster(home)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    worker = threading.Thread(target=run, name="mnemo-grok-roster", daemon=True)
    worker.start()
    worker.join(ROSTER_TIMEOUT if timeout is None else timeout)
    rows = box.get("rows")
    if worker.is_alive() or rows is None:
        return None
    return {str(getattr(r, "session_id", "")): r for r in rows if getattr(r, "session_id", None)}


# --------------------------------------------------------------------------
# The disk


def session_dir(child: Any, home: Path) -> Optional[Path]:
    """The child's session directory under ``home``, or ``None`` when it is not there."""
    from mnemo.core.grok import paths

    sid = str(child.session_id)
    cwd = getattr(child, "cwd", None)
    try:
        found = paths.session_dir(str(cwd), sid, home) if cwd else None
        return found if found is not None else paths.find_session_dir(sid, home)
    except Exception:  # noqa: BLE001
        return None


def _tail(path: Path, window: int) -> List[Dict[str, Any]]:
    """The last ``window`` bytes of a jsonl file as records; ``[]`` on any failure."""
    from mnemo.core.sessions.stalls import _tail_records

    return list(_tail_records(str(path), window=window))


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


class Phase:
    """What the session's own files say about its last turn."""

    __slots__ = ("state", "tempo", "needs", "open")

    def __init__(self, state: Optional[str] = None, tempo: Optional[str] = None,
                 needs: Optional[str] = None, open: bool = False):
        self.state = state
        self.tempo = tempo
        self.needs = needs
        #: The log started a turn (or a permission wait) and never ended it.
        self.open = open


def _permission_needs(tool: Any) -> str:
    from mnemo.core.grok.hooks import claude_tool_name

    name = _str(tool)
    if not name:
        return "permission requested"
    return f"permission requested: {claude_tool_name(name) or name}"


def disk_phase(directory: Optional[Path]) -> Phase:
    """The phase of the session in ``directory``, read from ``events.jsonl``.

    The last of ``turn_started``, ``permission_requested``,
    ``permission_resolved`` and ``turn_ended`` decides. An ``error`` outcome
    is a rate limit when ``updates.jsonl`` says the turn stopped on one
    (:func:`mnemo.core.sessions.stalls.grok_stop_reason`).
    """
    if directory is None:
        return Phase()
    for event in reversed(_tail(directory / EVENTS, EVENTS_WINDOW)):
        kind = event.get("type")
        if kind == "turn_ended":
            outcome = event.get("outcome")
            if outcome == "completed":
                return Phase("done", "idle")
            if outcome == "error":
                from mnemo.core.sessions.stalls import grok_stop_reason

                if grok_stop_reason(directory) == "rate_limit":
                    return Phase("blocked", "blocked", RATE_LIMITED_NEEDS)
                return Phase("blocked", "blocked", API_ERROR_NEEDS)
            # cancelled, or an outcome this was not measured against: the turn
            # ended without finishing, which is what `stopped` means.
            return Phase("stopped", "idle")
        if kind == "permission_requested":
            return Phase("blocked", "blocked", _permission_needs(event.get("tool_name")), open=True)
        if kind in ("turn_started", "permission_resolved"):
            return Phase("working", "active", open=True)
    return Phase()


def _iso_ms(ms: Any) -> Optional[str]:
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return None
    try:
        moment = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _iso(value: Any) -> Optional[str]:
    from mnemo.core.grok.transcript import _iso as grok_iso

    return grok_iso(value)


# --------------------------------------------------------------------------
# One child


def to_session(child: Any, *, home: Path, directory: Optional[Path],
               roster: Optional[Mapping[str, Any]]) -> Session:
    """One Grok child as a :class:`Session`, from its directory and its home's roster."""
    from mnemo.core.grok import paths

    sid = str(child.session_id)
    entry = roster.get(sid) if roster is not None else None
    summary = _read_json(directory / SUMMARY) if directory is not None else {}
    phase = disk_phase(directory)

    if roster is None:
        live: Optional[bool] = None
    else:
        live = bool(getattr(entry, "resident", False)) if entry is not None else False

    activity = getattr(entry, "activity", None) if entry is not None else None
    state, tempo, needs = phase.state, phase.tempo, phase.needs
    if activity == "working":
        state, tempo, needs = "working", "active", None
    elif activity == "needs_input":
        state, tempo = "blocked", "blocked"
        needs = phase.needs if phase.state == "blocked" else "waiting on you"
    elif roster is not None and (phase.open or state is None):
        # The leader answered and is running no turn of this session, yet
        # its log ended none: the turn stopped with it (or never started). A
        # record with no disk at all reads the same, so a child whose home is
        # gone is still filed as finished once its tree is.
        state, tempo, needs = "stopped", "idle", None

    updated = [u for u in (_iso(summary.get("updated_at")),
                           _iso_ms(getattr(entry, "last_change_ms", None))) if u]
    model = getattr(entry, "model_id", None) or _str(summary.get("current_model_id"))
    effort = getattr(entry, "effort", None) or _str(summary.get("reasoning_effort"))
    history = paths.chat_history_path(directory) if directory is not None else None

    return Session(
        short_id=_str(getattr(child, "short_id", None)) or paths.short_id(sid),
        state=state,
        tempo=tempo,
        needs=needs,
        detail=_str(summary.get("last_turn_summary")),
        name=_str(summary.get("generated_title")) or _str(getattr(entry, "title", None)),
        cwd=_str(getattr(child, "cwd", None)),
        session_id=sid,
        link_scan_path=str(history) if history is not None else None,
        updated_at=max(updated) if updated else None,
        live=live,
        model=_str(model),
        effort=_str(effort),
        agent=AGENT,
    )


def read(vault_root: Union[Path, str, None] = None, *,
         scope: Optional[str] = None) -> List[Session]:
    """Every Grok child as a :class:`Session`, in ledger order. Never raises.

    *scope* is a normalized cwd, as :func:`jobs.read_sessions` passes it;
    only children in its queue are read, and only their homes are asked.
    """
    from mnemo.core.sessions.jobs import in_scope

    found = children(vault_root)
    if scope is not None:
        found = [c for c in found if in_scope(getattr(c, "cwd", None), scope)]
    if not found:
        return []

    by_home: Dict[str, Tuple[Path, List[Any]]] = {}
    for child in found:
        home = home_of(child)
        key = os.path.normcase(os.path.realpath(str(home)))
        by_home.setdefault(key, (home, []))[1].append(child)

    out: List[Session] = []
    for home, members in by_home.values():
        # A home whose children's trees are all gone has nothing a roster
        # would change: their rows are finished history, read from disk.
        wanted = any(getattr(c, "cwd", None) and os.path.isdir(str(c.cwd)) for c in members)
        try:
            roster = read_roster(home) if wanted else None
        except Exception:  # noqa: BLE001
            roster = None
        for child in members:
            try:
                out.append(to_session(child, home=home, directory=session_dir(child, home),
                                      roster=roster))
            except Exception:  # noqa: BLE001 — one child must not take out the queue
                continue
    return out


# --------------------------------------------------------------------------
# For the views: a Grok history in the shape the activity readers know


#: Grok's input keys under the names Claude Code's tools use, for the targets
#: :mod:`mnemo.core.activity.summarize` prints.
_INPUT_KEYS = {"target_file": "file_path"}


def claude_shaped_events(path: Optional[str]) -> List[Dict[str, Any]]:
    """A Grok ``chat_history.jsonl`` as Claude-Code-shaped events, tools renamed.

    :func:`mnemo.core.grok.transcript.load_events` does the shape; this puts
    each tool under the Claude name it stands for (``run_terminal_command`` →
    ``Bash``) and its file under ``file_path``, so ``recent_actions`` and the
    queue's activity column name a Grok child's actions as they name a
    Claude child's. ``[]`` when the file cannot be read.
    """
    if not path:
        return []
    from mnemo.core.grok.hooks import claude_tool_name
    from mnemo.core.grok.transcript import load_events

    try:
        events = load_events(Path(path))
    except Exception:  # noqa: BLE001
        return []
    for event in events:
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else ():
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            block["name"] = claude_tool_name(block.get("name") or "") or block.get("name")
            tool_input = block.get("input")
            if isinstance(tool_input, dict):
                for grok_key, claude_key in _INPUT_KEYS.items():
                    if grok_key in tool_input and claude_key not in tool_input:
                        tool_input[claude_key] = tool_input[grok_key]
    return events


def activities(sessions: Iterable[Any]) -> Dict[str, Any]:
    """``short_id -> Activity`` for Grok sessions, from a cold read of each history."""
    from mnemo.core.activity.summarize import summarize

    out: Dict[str, Any] = {}
    for session in sessions:
        try:
            events = claude_shaped_events(getattr(session, "link_scan_path", None))
            act = summarize(events) if events else None
        except Exception:  # noqa: BLE001
            continue
        if act is not None:
            out[session.short_id] = act
    return out


def home_of_session(session: Any) -> Optional[Path]:
    """The home a Grok :class:`Session` was read from, off its history's path.

    ``<home>/sessions/<cwd>/<id>/chat_history.jsonl``; ``None`` without one.
    """
    path = getattr(session, "link_scan_path", None)
    if not path:
        return None
    parents = Path(path).parents
    return parents[3] if len(parents) > 3 else None


def attach_command(session: Any) -> str:
    """The command that opens a Grok child in a terminal.

    ``grok --resume <id>`` from the child's tree, with the home named when it
    is not the default one. Read from ``grok --help`` (1.0.60): a UUID-shaped
    value always means an id.
    """
    parts = []
    cwd = getattr(session, "cwd", None)
    if cwd:
        parts.append(f"cd {_quote(cwd)} &&")
    home = home_of_session(session)
    default = Path.home() / ".grok"
    if home is not None and os.path.normcase(str(home)) != os.path.normcase(str(default)):
        parts.append(f"GROK_HOME={_quote(str(home))}")
    parts.append(f"grok --resume {getattr(session, 'session_id', None) or session.short_id}")
    return " ".join(parts)


_SAFE = re.compile(r"^[A-Za-z0-9_./:@%+=,-]+$")


def _quote(text: str) -> str:
    return text if _SAFE.match(text) else "'" + text.replace("'", "'\"'\"'") + "'"
