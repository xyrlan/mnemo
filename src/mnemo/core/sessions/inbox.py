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
let a socket marker stand in for the user (``design/specs/
2026-09-15-inbox-reply-authority.md``). This module carries "a child of yours
finished"; it must not carry approval, and callers must not phrase it as one.

**Why no token is kept (#553).** Rows used to carry the session's
``CLAUDE_CODE_MESSAGING_TOKEN``, in plaintext, in a file nothing pruned. That
token is not needed and not ours to present:

- Claude Code requires the auth line only on Windows (``authRequired =
  platform === "windows"`` in 2.1.284; elsewhere a peer without it is
  "accepted: auth is optional on this platform"). On macOS and Linux Claude
  Code chmods the socket ``0600`` (``/tmp/cc-socks`` is ``0700`` on macOS),
  so whoever can read the vault as this user can already connect; the token
  added no protection, only a copy of a secret on disk. ``mnemo-desktop``
  has always replied without one (``mission.rs`` ``reply`` passes ``None``),
  verified live 2026-09-15.
- On Windows, where it is required, Claude Code's inbox is a named pipe
  (``\\\\.\\pipe\\…``), not an ``AF_UNIX`` path named after a pid: mnemo
  never had a working row there to present it with.
- The env value is the session's *child* token — Claude Code publishes a
  separate *peer* key under ``~/.claude/sessions/`` — and presenting it tells
  the inbox the writer descends from that session. A sibling's ``SessionEnd``
  does not; mnemo writes as the peer it is.

So a row holds the address and nothing that authenticates, the file is
written ``0600``, and every :func:`record` rewrites it without any legacy
``token`` field and without rows whose pid has exited — the upgrade scrubs
itself at the first session start.

Everything here fails open. A notice that cannot be delivered costs the
maintainer the convenience he had before this existed; it must never cost a
session its ``SessionEnd``.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Mapping, Optional, Set

#: Claude Code's own names for the session's inbox, exported into its env.
#: The token beside the socket (``CLAUDE_CODE_MESSAGING_TOKEN``) is
#: deliberately not read: see the module docstring (#553).
SOCKET_ENV = "CLAUDE_CODE_MESSAGING_SOCKET"
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"

#: One JSON object per session start, append-only like ``dispatch-parents``.
#: Latest line for a session id wins, so a re-used id (resume) re-addresses
#: itself rather than leaving a stale row in front.
LOG_NAME = "session-inbox.jsonl"

#: Serialises :func:`record`'s compaction against another session's, so two
#: rewrites never race; appends need no lock (#586).
LOCK_NAME = "session-inbox.lock"

#: Fields a row may carry. Anything else — the ``token`` rows written before
#: #553 above all — is dropped when the file is rewritten.
ROW_FIELDS = ("session_id", "socket", "pid", "pid_start")

#: How long :func:`record` waits for the lock before leaving the compaction
#: to the next writer. Its row is already on disk by then.
LOCK_WAIT_SECONDS = 2.0

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
    socket to write into, and the pid plus its start time, which together say
    whether the socket name still means the same process. No token (#553).
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
        "pid": pid,
        "pid_start": pid_start(pid),
    }


def live_pids() -> Optional[Set[int]]:
    """Every pid running now, or ``None`` when that cannot be read.

    One ``ps`` for the whole table rather than one per row. ``None`` means
    "unknown", and callers must then keep every row: pruning on a failed read
    would forget live parents.
    """
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid="],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    pids = set()
    for tok in (out.stdout or "").split():
        try:
            pids.add(int(tok))
        except ValueError:
            continue
    return pids or None


def _clean(row: dict) -> dict:
    return {k: row[k] for k in ROW_FIELDS if k in row}


def compact(lines, alive: Optional[Set[int]]) -> list:
    """The rows worth keeping from *lines*, each reduced to :data:`ROW_FIELDS`.

    Drops unparsable lines, every field outside the row shape (so a legacy
    ``token`` never survives a rewrite) and, when *alive* is known, rows whose
    pid has exited — such a row can never pass :func:`is_live` again, since a
    pid that comes back is a different process with a different start time.
    """
    kept = []
    for line in lines:
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(row, dict) or not row.get("session_id"):
            continue
        if alive is not None:
            try:
                if int(row.get("pid") or 0) not in alive:
                    continue
            except (TypeError, ValueError):
                continue
        kept.append(_clean(row))
    return kept


def _dump(rows) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")


def _read(path: Path) -> bytes:
    try:
        with path.open("rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return b""


def _lines(data: bytes) -> list:
    return data.decode("utf-8", errors="replace").splitlines()


def _append(path: Path, row: dict) -> None:
    """One ``O_APPEND`` write of one line: never interleaves with another's."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as fh:
        fh.write(_dump([row]))


#: How often :func:`_rewrite` re-reads a log that keeps growing under it
#: before it leaves the compaction to the next writer.
REWRITE_TRIES = 5


def _rewrite(path: Path, row: dict) -> None:
    """Compact the log in place, under the lock, without losing an append.

    Writers append outside the lock (:func:`record`), so the file only grows
    while this runs. Two rules keep every appended row (#586):

    - Only rows already on disk *before* ``ps`` ran are pruned by its answer.
      A row appended after that read belongs to a process that may have
      started after the snapshot: absent from it, but alive.
    - The rewrite is installed only if the file is still byte-for-byte what
      was compacted. Otherwise it is read again; after :data:`REWRITE_TRIES`
      it is left uncompacted, which costs disk, not a row.
    """
    head = _read(path)
    alive = live_pids()
    for _ in range(REWRITE_TRIES):
        data = _read(path)
        if not data.startswith(head):
            return  # rewritten by someone else: their copy stands
        rows = compact(_lines(head), alive) + compact(_lines(data[len(head):]), None)
        if row not in rows:
            rows.append(row)
        rows = [r for i, r in enumerate(rows) if r not in rows[i + 1:]]
        if _read(path) != data:
            continue
        from mnemo.core import atomic

        atomic.atomic_write_bytes(path, _dump(rows))
        return


def record(vault_root: Path | str, address: dict | None) -> None:
    """Add one address row, then compact the log. Never raises.

    The row is appended first, in one ``O_APPEND`` write, so it is on disk
    before anything else can go wrong. The compaction — no legacy token, no
    row whose pid is gone, no exact duplicate, ``0600`` — runs after, under
    :data:`LOCK_NAME`, and keeps every row appended while it ran
    (:func:`_rewrite`). A lock that stays busy past :data:`LOCK_WAIT_SECONDS`
    skips the compaction, not the row.

    Before #586 the row was written *by* the locked rewrite, with an unlocked
    append as the fallback when the lock stayed busy; a locked writer that had
    read the file before that append then installed its copy over it. On
    2026-10-07 every session resumed at once after a restart, one parent's
    ``SessionStart`` ran for 38 s, and that parent had no row: its four
    children's notices all went undelivered.
    """
    if not address:
        return
    try:
        from mnemo.core import locks

        row = _clean(address)
        path = log_path(vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        _append(path, row)
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            with locks.try_lock(path.parent / LOCK_NAME, stale_after=30.0) as held:
                if held:
                    _rewrite(path, row)
                    return
            if time.monotonic() >= deadline:
                return
            time.sleep(0.05)
    except (OSError, TypeError, ValueError):
        return


def ensure_recorded(vault_root: Path | str, env: Mapping[str, str] | None = None) -> bool:
    """Record this session's address if the log has no row for it (#586).

    ``SessionStart`` is not the only chance any more: one lost row used to
    silence a parent for the rest of its session. Run before each prompt, so
    the common case is one read of a small file and no ``ps``. True when a
    row was written. Never raises.
    """
    try:
        src = os.environ if env is None else env
        sid = (src.get(SESSION_ENV) or "").strip()
        sock = (src.get(SOCKET_ENV) or "").strip()
        if not sid or not sock:
            return False
        for line in _lines(_read(log_path(vault_root))):
            if sid in line and sock in line:
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(row, dict) and row.get("session_id") == sid \
                        and row.get("socket") == sock:
                    return False
        address = address_from_env(src)
        if not address:
            return False
        record(vault_root, address)
        return True
    except Exception:  # noqa: BLE001 — runs in a prompt hook
        return False


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
    it (``src-tauri/src/mission.rs`` ``post_message``): a newline-terminated
    ``stream-json`` user turn. No auth line: optional on every platform this
    reaches, and the token it would need is not ours (#553, module docstring).
    """
    sock_path = (address or {}).get("socket") or ""
    if not sock_path or not text:
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(TIMEOUT_SECONDS)
            s.connect(sock_path)
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
