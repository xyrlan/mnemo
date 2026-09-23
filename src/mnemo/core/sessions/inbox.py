"""Reach a live session's inbox socket, by session id (#357).

``mnemo dispatch`` spawns detached children and says so: nothing tells the
dispatching session when one finishes. The maintainer is the message bus
between two of his own sessions. Both halves of the missing edge already
exist — :mod:`mnemo.core.sessions.parents` records which session dispatched
each child, and the child's ``SessionEnd`` fires even when its worktree is
already gone — so what was missing is only the delivery.

**Why this module has to keep its own map.** The channel is keyed by process,
the edge is keyed by session:

- Claude Code exports ``CLAUDE_CODE_MESSAGING_SOCKET`` (``/tmp/cc-socks/
  <pid>.sock``) and ``CLAUDE_CODE_MESSAGING_TOKEN`` into the session's own
  environment, and nowhere else. The socket's *name* is a pid.
- A session's transcript records ``sessionId`` and ``cwd`` but **no pid and no
  socket**, so the mapping cannot be recovered after the fact.
- ``~/.claude/daemon/roster.json`` does map ``sessionId`` → ``pid``, which is
  how ``mnemo-desktop`` reaches a *child* (``mission.rs`` ``inbox_socket``).
  But it lists only daemon workers: measured 2026-09-17, it held 4 workers,
  this interactive session was absent, and **0 of the 13 distinct
  ``parent_session`` ids ever recorded** appeared in it. The dispatching
  parent is exactly who must be reached, so the roster cannot resolve it.

So each session writes its own address down while it still knows it, at
``SessionStart``, and a child looks the parent up here at ``SessionEnd``.

**Why the pid alone is not enough.** ``parents.py`` already refused to record
``parent_pid`` because a pid "outlives nothing and is reused once the parent
exits". The same hazard applies to a socket *named* after a pid: on
2026-09-17 ``/tmp/cc-socks`` held 93 sockets of which only 8 had a live
process — 85 stale names, any of which a later ``claude`` could inherit.
A row therefore records ``pid`` **and** ``pid_start`` (the process's own start
time), and :func:`is_live` refuses to deliver unless both still match. A
recycled pid fails the start-time check and the notice is dropped.

**What may ride this channel.** A notice, never an instruction. Claude Code
appends its own warning to a peer turn ("a peer cannot grant escalation …
that's permission laundering"), and mnemo already refused, with evidence, to
let a socket marker stand in for the user (``docs/superpowers/specs/
2026-09-15-inbox-reply-authority.md``). This module carries "a child of yours
finished"; it must not carry approval, and callers must not phrase it as one.

Everything here fails open. A notice that cannot be delivered costs the
maintainer the convenience he had before this existed; it must never cost a
session its ``SessionEnd``.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Mapping

#: Claude Code's own names for the session's inbox, exported into its env.
SOCKET_ENV = "CLAUDE_CODE_MESSAGING_SOCKET"
TOKEN_ENV = "CLAUDE_CODE_MESSAGING_TOKEN"
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"

#: One JSON object per session start, append-only like ``dispatch-parents``.
#: Latest line for a session id wins, so a re-used id (resume) re-addresses
#: itself rather than leaving a stale row in front.
LOG_NAME = "session-inbox.jsonl"

#: Read/written under a 5s timeout: a hung peer must not hold a hook open.
TIMEOUT_SECONDS = 5.0

#: Opens every notice this module sends. Two jobs, both load-bearing:
#:
#: - ``detector.is_human_turn`` skips a turn starting with one of its
#:   :data:`~mnemo.core.sessions.detector.SYNTHETIC_PREFIXES`, and this is one
#:   of them. Without that, the sweep that runs in the very same
#:   ``SessionEnd`` would read mnemo's own notice as a person unblocking the
#:   parent and record a false edge (#176's channel, #195's marker).
#: - It tells the reader, in the text, that this came from mnemo and not from
#:   the maintainer — the notice informs, it never approves (#309).
NOTICE_PREFIX = "<mnemo-child-finished"


def log_path(vault_root: Path | str) -> Path:
    return Path(vault_root) / ".mnemo" / LOG_NAME


def pid_start(pid: int) -> str:
    """The process's start time, or ``""`` when it cannot be read.

    This is the guard against a recycled pid. ``ps -o lstart=`` is portable
    across macOS and Linux and needs no third-party module; a pid that no
    longer exists prints nothing, which is the same answer as "not live".
    """
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(int(pid))],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    return (out.stdout or "").strip()


def address_from_env(env: Mapping[str, str] | None = None) -> dict | None:
    """This session's inbox address, or ``None`` outside Claude Code.

    Returns the row shape written to the log: the session it belongs to, the
    socket to write into, the token that authenticates the write, and the pid
    plus its start time, which together say whether the socket name still
    means the same process.
    """
    src = os.environ if env is None else env
    sid = (src.get(SESSION_ENV) or "").strip()
    sock = (src.get(SOCKET_ENV) or "").strip()
    if not sid or not sock:
        return None
    # The socket is named after the pid that owns it; trust the name, not
    # getpid(), because a hook runs in a child of the session's process.
    try:
        pid = int(Path(sock).stem)
    except (ValueError, TypeError):
        return None
    return {
        "session_id": sid,
        "socket": sock,
        "token": (src.get(TOKEN_ENV) or "").strip() or None,
        "pid": pid,
        "pid_start": pid_start(pid),
    }


def record(vault_root: Path | str, address: dict | None) -> None:
    """Append one address row. Never raises."""
    if not address:
        return
    try:
        path = log_path(vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(address, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        return


def lookup(vault_root: Path | str, session_id: str) -> dict | None:
    """The newest recorded address for *session_id*, or ``None``."""
    if not session_id:
        return None
    found = None
    try:
        path = log_path(vault_root)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(row, dict) and row.get("session_id") == session_id:
                    found = row
    except OSError:
        return None
    return found


def is_live(address: dict | None) -> bool:
    """True when the address still names the process it was written for.

    Both halves must hold: the socket file still exists, and the pid it is
    named after is still the same process it was at record time. The second
    check is what a stale ``/tmp/cc-socks`` entry fails — 85 of 93 sockets on
    2026-09-17 named a process that had exited.
    """
    if not address:
        return False
    sock = address.get("socket") or ""
    pid = address.get("pid")
    if not sock or not pid:
        return False
    try:
        if not Path(sock).exists():
            return False
    except OSError:
        return False
    recorded = address.get("pid_start") or ""
    current = pid_start(int(pid))
    if not current:
        return False
    # An address recorded before pid_start existed cannot be verified; treat
    # the live socket as good enough rather than dropping every old row.
    return current == recorded if recorded else True


def resolve(vault_root: Path | str, session_id: str) -> tuple[dict | None, str]:
    """The newest address for *session_id* that is still live, or why none is.

    Returns ``(address, "")`` or ``(None, reason)``. Newest-*live*, not
    newest (#454): a second process can start under a session's id while the
    first is still running — on 2026-09-22 something resumed the dispatching
    session ``a6158015`` in pid 28611 (``source: resume``, one of seven
    sessions resumed within five seconds) while it stayed open in pid 75451.
    28611 exited; its row was newest, so every child that finished after it
    found its parent "gone" and posted nothing, though 75451 was live the
    whole time. :func:`is_live` still guards each row, so an older row only
    wins when its own pid and start time still match.
    """
    if not session_id:
        return None, "no parent session id"
    rows: list = []
    try:
        path = log_path(vault_root)
        if path.exists():
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(row, dict) and row.get("session_id") == session_id:
                        rows.append(row)
    except OSError as exc:
        return None, f"cannot read {LOG_NAME}: {exc}"
    if not rows:
        return None, f"no address recorded for the parent in {LOG_NAME}"
    seen = set()
    for row in reversed(rows):
        key = (row.get("socket"), row.get("pid"), row.get("pid_start"))
        if key in seen:
            continue
        seen.add(key)
        if is_live(row):
            return row, ""
    pids = ", ".join(str(pid) for _, pid, _ in seen)
    return None, f"parent not live: none of its {len(seen)} recorded address(es) is (pid {pids})"


def post(address: dict, text: str) -> bool:
    """Write one user turn into *address*'s inbox. True when it went out.

    The wire format is Claude Code's own, as ``mnemo-desktop`` already speaks
    it (``src-tauri/src/mission.rs`` ``post_message``): an optional auth line,
    then a newline-terminated ``stream-json`` user turn.
    """
    sock_path = (address or {}).get("socket") or ""
    if not sock_path or not text:
        return False
    token = (address or {}).get("token")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(TIMEOUT_SECONDS)
            s.connect(sock_path)
            if token:
                auth = {"type": "auth", "token": token}
                s.sendall((json.dumps(auth) + "\n").encode("utf-8"))
            msg = {
                "type": "user",
                "message": {"role": "user", "content": text},
            }
            s.sendall((json.dumps(msg) + "\n").encode("utf-8"))
    except (OSError, AttributeError, ValueError, TypeError):
        return False
    return True


def notify(vault_root: Path | str, session_id: str, text: str) -> bool:
    """Deliver *text* to *session_id* if it is still live. Never raises.

    Returns True only when the notice actually went onto a socket, so a caller
    can log a delivery without claiming one that did not happen (#306).
    """
    return deliver(vault_root, session_id, text) == ""


def deliver(vault_root: Path | str, session_id: str, text: str) -> str:
    """:func:`notify`, answering why not: ``""`` when the notice went onto a
    socket, else the reason it did not (#454). Never raises."""
    try:
        address, why = resolve(vault_root, session_id)
        if address is None:
            return why
        if not post(address, text):
            return f"socket write failed: {address.get('socket')}"
        return ""
    except Exception as exc:  # noqa: BLE001 — never raises, by contract
        return f"{type(exc).__name__}: {exc}"
