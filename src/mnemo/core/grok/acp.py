"""mnemo's one client of Grok Build's leader (ACP over ``grok agent --leader stdio``).

Everything in mnemo and mnemo-desktop that reaches a live Grok session goes
through :class:`Leader` here, or through ``mnemo grok`` which wraps it
(``design/specs/2026-10-16-grok-host.md``, decision 4).

The transport is the one Grok's own clients use: ``grok agent --leader stdio``
bridges JSON-RPC, one message per line, to a per-home leader daemon, spawning
the daemon in its own process group when none listens. The daemon keeps a
session's turn running after the bridge exits, so every call here opens a
bridge, says what it has to say and leaves.

Measured live on grok 1.0.60 (2026-10-16, free account; the probes are
``tests/live/test_grok_leader_live.py``) and read from the Apache-2.0 source
(``github.com/xai-org/grok-build`` at ``2bdd1d6``):

- ``session/new`` takes ``_meta.yoloMode``, ``_meta.modelId`` and
  ``_meta.reasoningEffort`` (``xai-grok-shell/src/agent/mvp_agent/
  session_setup.rs``). With ``yoloMode`` the session runs its tools with no
  client attached: a tool call that began after the last client left ran.
- An unknown ``modelId`` is not an error: the session silently gets the
  default model. :meth:`Leader.new_session` refuses it instead.
- Grok does not persist yolo. ``session/load`` re-reads it from its own
  ``_meta`` (else the config default), and a dormant row reports ``yolo:
  false``. mnemo records the sessions it created with yolo
  (:func:`yolo_ledger_path`) and asks for yolo again when it reloads one.
- ``session/prompt`` on a resident session (``working``, ``idle``,
  ``needs_input``) works from any client with no ``session/load``; on a
  dormant one it answers ``-32602 unknown session id`` until the session is
  loaded. A prompt sent while a turn runs is queued, and the leader runs it
  after the client is gone.
- ``_meta.promptId`` on ``session/prompt`` becomes the id the leader reports
  in ``_x.ai/queue/changed`` (``entries[].id`` while queued,
  ``runningPromptId`` once running) and in every ``session/update``'s
  ``_meta.promptId``: that is how a client knows its own turn started.
- Extension methods need the leading underscore on the wire
  (``_x.ai/sessions/list``); their result is wrapped as ``{"result": …,
  "error"?: …}`` (``ExtMethodResult``), except ``_x.ai/billing``'s.
- A home with no login answers ``-32000 Authentication required``; an
  account past its usage answers ``session/prompt`` with ``Rate limited``
  (free tier: 600k tokens over a rolling 24 hours, measured when it ran out).
- The leader socket is ``$GROK_LEADER_SOCKET`` when set, else
  ``<home>/leader.sock``; macOS refuses one longer than 103 bytes
  (:func:`leader_socket_for` picks a short stable one instead).
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from mnemo.core.grok import paths

PROTOCOL_VERSION = 1

#: Efforts Grok's ``ReasoningEffort`` parses (``xai-grok-sampling-types``).
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

#: Seconds a bridge gets to answer ``initialize``: it may be starting the daemon.
CONNECT_TIMEOUT = 30.0
#: Seconds a plain request (roster, close, billing, new, load) gets.
REQUEST_TIMEOUT = 90.0

#: Variables naming the session a process runs inside. A leader the bridge
#: starts inherits the bridge's environment and hands it to every session it
#: hosts, so these would make each Grok child look like it runs inside its
#: dispatcher.
SESSION_ENV = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "GROK_SESSION_ID",
    "GROK_HOOK_EVENT",
    "GROK_WORKSPACE_ROOT",
)


class GrokUnavailable(RuntimeError):
    """Grok cannot do what was asked; the message says what a person can do."""


class AcpError(GrokUnavailable):
    """The leader answered a request with a JSON-RPC error."""

    def __init__(self, method: str, error: Mapping[str, Any]):
        self.method = method
        self.code = error.get("code")
        self.error_message = str(error.get("message") or "")
        self.data = error.get("data")
        detail = self.data if isinstance(self.data, str) else json.dumps(self.data) if self.data else ""
        text = f"Grok refused {method}: {self.error_message}"
        if detail:
            text += f" ({detail})"
        super().__init__(text)


class RateLimited(AcpError):
    """The account hit its usage limit (``Rate limited``, HTTP 429 behind it)."""


class SessionNotFound(GrokUnavailable):
    """No session with that id in this home, resident or on disk."""


@dataclass(frozen=True)
class RosterEntry:
    """One row of the leader's roster: the live state of one session."""

    session_id: str
    cwd: str
    activity: str
    resident: bool
    model_id: Optional[str]
    effort: Optional[str]
    title: Optional[str]
    last_change_ms: Optional[int]
    home: Path


# --------------------------------------------------------------------------
# Where the leader listens


def state_dir() -> Path:
    """mnemo's own Grok state: the yolo ledger, and sockets of homes too deep for theirs."""
    return Path.home() / ".mnemo" / "grok"


def socket_limit(platform: Optional[str] = None) -> int:
    """Longest Unix socket path the platform binds, in bytes (``sun_path`` less its NUL)."""
    platform = sys.platform if platform is None else platform
    return 103 if platform == "darwin" or "bsd" in platform else 107


def _fits(path: str, limit: int) -> bool:
    return len(os.fsencode(path)) <= limit


def _home_key(home: Path) -> str:
    resolved = os.path.normcase(os.path.realpath(str(home)))
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]


def _same_home(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


def leader_socket_for(home: Path, env: Optional[Mapping[str, str]] = None,
                      *, platform: Optional[str] = None) -> str:
    """The socket mnemo talks to ``home``'s leader on. Stable per home.

    ``$GROK_LEADER_SOCKET`` when it is set and ``home`` is the environment's
    own home; else Grok's default ``<home>/leader.sock`` when it fits the
    platform's limit, so ``grok leader list/kill`` and the person's own
    ``grok`` find the same leader. A home too deep for that gets
    ``<state_dir()>/leader-<key>.sock`` (``~/.mnemo/grok``), or ``<tmp>/mnemo-grok-<uid>/
    leader-<key>.sock`` when even that is too long, ``<key>`` being a hash of
    the home's resolved path: every mnemo process and the desktop compute the
    same one.
    """
    env = os.environ if env is None else env
    limit = socket_limit(platform)
    named = (env.get(paths.LEADER_SOCKET_ENV) or "").strip()
    if named and _same_home(home, paths.grok_home(env)):
        return named
    default = str(home / "leader.sock")
    if _fits(default, limit):
        return default
    name = f"leader-{_home_key(home)}.sock"
    candidates = [state_dir() / name]
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    candidates.append(Path(tempfile.gettempdir()) / f"mnemo-grok-{uid}" / name)
    for candidate in candidates:
        if _fits(str(candidate), limit):
            return str(candidate)
    return str(Path("/tmp") / f"mnemo-grok-{uid}" / name)


def _prepare_socket_dir(socket: str, home: Path) -> None:
    """Make the directory a non-default socket goes in, private to this user."""
    parent = Path(socket).parent
    if _same_home(parent, home):
        return
    try:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise GrokUnavailable(f"cannot make the directory for Grok's leader socket {socket}: {exc}") from exc


# --------------------------------------------------------------------------
# Sessions mnemo created with yolo


def yolo_ledger_path() -> Path:
    """Where mnemo records the sessions it created with yolo (one JSON line each)."""
    return state_dir() / "yolo-sessions.jsonl"


def _yolo_key(home: Path, session_id: str) -> str:
    return f"{_home_key(home)}:{session_id}"


def _record_yolo(home: Path, session_id: str) -> None:
    path = yolo_ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"home": os.path.realpath(str(home)), "session_id": session_id}) + "\n")
    except OSError:
        # Without the line a reload of this session asks Grok's default
        # permission mode; the session itself is fine.
        pass


def _yolo_sessions() -> Set[str]:
    out: Set[str] = set()
    try:
        lines = yolo_ledger_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
            out.add(_yolo_key(Path(row["home"]), row["session_id"]))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def was_created_yolo(home: Path, session_id: str) -> bool:
    return _yolo_key(home, session_id) in _yolo_sessions()


# --------------------------------------------------------------------------
# One bridge process


def _grok_argv() -> List[str]:
    """The command that opens a bridge to the leader. Tests replace it."""
    binary = shutil.which("grok")
    if binary is None:
        raise GrokUnavailable(
            "grok is not on PATH: install Grok Build (xAI's `grok` CLI) and "
            "make sure `grok --version` runs in this shell")
    return [binary, "agent", "--leader", "stdio"]


def has_login(home: Path, env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether ``home`` holds a login (``auth.json``; never read) or the env carries a key."""
    env = os.environ if env is None else env
    return (home / "auth.json").is_file() or bool((env.get("XAI_API_KEY") or "").strip())


def _is_auth_error(error: Mapping[str, Any]) -> bool:
    return "authentication required" in str(error.get("message") or "").lower()


def _version() -> str:
    from mnemo import __version__

    return __version__


class _Bridge:
    """One ``grok agent --leader stdio`` process and the messages it prints."""

    def __init__(self, argv: Sequence[str], env: Mapping[str, str], cwd: str):
        try:
            self._proc = subprocess.Popen(
                list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=dict(env), cwd=cwd,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise GrokUnavailable(f"cannot run {argv[0]}: {exc}") from exc
        self._inbox: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._stderr: List[bytes] = []
        self._next_id = 0
        self._lock = threading.Lock()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(msg, dict):
                self._inbox.put(msg)
        self._inbox.put(None)

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        for raw in self._proc.stderr:
            self._stderr.append(raw)
            del self._stderr[:-20]

    def stderr_tail(self) -> str:
        return b"".join(self._stderr).decode("utf-8", "replace").strip()[-600:]

    def _write(self, msg: Mapping[str, Any]) -> None:
        line = (json.dumps(msg) + "\n").encode("utf-8")
        assert self._proc.stdin is not None
        with self._lock:
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise GrokUnavailable(f"Grok's leader bridge closed: {self.stderr_tail() or exc}") from exc

    def send(self, method: str, params: Mapping[str, Any]) -> int:
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": dict(params)})
        return self._next_id

    def next_message(self, deadline: float) -> Optional[Dict[str, Any]]:
        """The next message, ``{}`` when ``deadline`` passed first, ``None`` at EOF."""
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return {}
            try:
                msg = self._inbox.get(timeout=min(left, 0.5))
            except queue.Empty:
                continue
            if msg is None:
                self._inbox.put(None)
                return None
            if "method" in msg and "id" in msg:
                self._answer_request(msg)
                continue
            return msg

    def _answer_request(self, msg: Mapping[str, Any]) -> None:
        # A permission request goes to every attached client and the first
        # answer wins: left unanswered it stays for the person (or another
        # client) to decide. Anything else this client does not serve.
        if msg.get("method") == "session/request_permission":
            return
        self._write({"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32601, "message": "Method not found"}})

    def request(self, method: str, params: Mapping[str, Any], timeout: float,
                on_message: Optional[Callable[[Dict[str, Any]], None]] = None) -> Any:
        """Send ``method`` and return its result; raise on error, EOF or timeout."""
        rid = self.send(method, params)
        deadline = time.monotonic() + timeout
        while True:
            msg = self.next_message(deadline)
            if msg is None:
                raise GrokUnavailable(
                    f"Grok's leader bridge exited during {method}: {self.stderr_tail() or 'no output'}")
            if not msg:
                raise TimeoutError(f"Grok's leader did not answer {method} within {timeout:.0f}s")
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    raise AcpError(method, msg["error"] or {})
                return msg.get("result")
            if on_message is not None:
                on_message(msg)

    def close(self) -> None:
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def _unwrap(method: str, result: Any) -> Any:
    """Strip the ``{"result": …, "error"?: …}`` envelope Grok's extensions answer in."""
    if isinstance(result, dict) and result and set(result) <= {"result", "error"}:
        if result.get("error"):
            error = result["error"]
            raise AcpError(method, error if isinstance(error, dict) else {"message": str(error)})
        return result.get("result")
    return result


# --------------------------------------------------------------------------
# The client


def _text_of(update: Mapping[str, Any]) -> str:
    content = update.get("content")
    if isinstance(content, dict) and content.get("type") == "text":
        return str(content.get("text") or "")
    return ""


def _prompt_id_of(msg: Mapping[str, Any]) -> Optional[str]:
    params = msg.get("params") or {}
    meta = params.get("_meta") if isinstance(params, dict) else None
    if not isinstance(meta, dict):
        meta = msg.get("_meta") if isinstance(msg.get("_meta"), dict) else {}
    value = meta.get("promptId")
    return str(value) if value else None


class _Turn:
    """What one client sees of one prompt it sent."""

    def __init__(self, session_id: str, prompt_id: str):
        self.session_id = session_id
        self.prompt_id = prompt_id
        self.started = False
        self.segments: List[str] = [""]

    def see(self, msg: Mapping[str, Any]) -> None:
        method = msg.get("method")
        params = msg.get("params") or {}
        if not isinstance(params, dict) or params.get("sessionId") != self.session_id:
            return
        if method == "_x.ai/queue/changed":
            if params.get("runningPromptId") == self.prompt_id:
                self.started = True
            elif any(isinstance(e, dict) and e.get("id") == self.prompt_id
                     for e in params.get("entries") or ()):
                # Queued behind the running turn: the leader runs it with no
                # client attached (measured), so it counts as taken.
                self.started = True
            return
        if method != "session/update":
            return
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk" and _prompt_id_of(msg) == self.prompt_id:
            self.started = True
            self.segments[-1] += _text_of(update)
        elif kind == "tool_call" and self.started and self.segments[-1]:
            self.segments.append("")

    def text(self) -> str:
        """The model's last message of the turn: what it said after its last tool call."""
        for segment in reversed(self.segments):
            if segment.strip():
                return segment.strip()
        return ""


class Leader:
    """The leader of one Grok home (one account): ``home`` or ``$GROK_HOME``/``~/.grok``."""

    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home).expanduser() if home is not None else paths.grok_home()

    # -- plumbing ---------------------------------------------------------

    @property
    def socket(self) -> str:
        return leader_socket_for(self.home)

    def _env(self) -> Dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in SESSION_ENV}
        env[paths.HOME_ENV] = str(self.home)
        socket = self.socket
        if socket == str(self.home / "leader.sock"):
            env.pop(paths.LEADER_SOCKET_ENV, None)
        else:
            env[paths.LEADER_SOCKET_ENV] = socket
        return env

    def _connect(self) -> _Bridge:
        argv = _grok_argv()
        env = self._env()
        if not self.home.is_dir():
            raise GrokUnavailable(f"Grok home {self.home} does not exist: log in with "
                                  f"`GROK_HOME={self.home} grok login`")
        if not has_login(self.home, env):
            raise GrokUnavailable(f"Grok home {self.home} has no login: run "
                                  f"`GROK_HOME={self.home} grok login`")
        if env.get(paths.LEADER_SOCKET_ENV):
            _prepare_socket_dir(env[paths.LEADER_SOCKET_ENV], self.home)
        bridge = _Bridge(argv, env, cwd=str(self.home))
        try:
            bridge.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                       "terminal": False},
                "clientInfo": {"name": "mnemo", "version": _version()},
            }, timeout=CONNECT_TIMEOUT)
        except (GrokUnavailable, TimeoutError) as exc:
            tail = bridge.stderr_tail()
            bridge.close()
            raise GrokUnavailable(
                f"Grok's leader for {self.home} did not come up (socket {self.socket}): "
                f"{tail or exc}. Run `grok agent --leader stdio` with GROK_HOME={self.home} "
                "to see why") from exc
        return bridge

    def _request(self, bridge: _Bridge, method: str, params: Mapping[str, Any],
                 timeout: float = REQUEST_TIMEOUT, **kw: Any) -> Any:
        try:
            return bridge.request(method, params, timeout, **kw)
        except AcpError as exc:
            if _is_auth_error({"message": exc.error_message}):
                raise GrokUnavailable(
                    f"Grok home {self.home} is not logged in: run "
                    f"`GROK_HOME={self.home} grok login`") from exc
            raise

    # -- the contract ------------------------------------------------------

    def new_session(self, cwd: str, *, model: Optional[str] = None,
                    effort: Optional[str] = None, yolo: bool = True) -> str:
        """Create a session in ``cwd`` and return its id.

        The session lives in the leader, not in this process: it outlives the
        call. With ``yolo`` it approves its own tools, so a turn runs to its
        end with nobody attached.
        """
        if effort is not None and effort not in EFFORTS:
            raise ValueError(f"Grok takes no effort {effort!r}; it takes {', '.join(EFFORTS)}")
        meta: Dict[str, Any] = {}
        if yolo:
            meta["yoloMode"] = True
        if model:
            meta["modelId"] = model
        if effort:
            meta["reasoningEffort"] = effort
        params: Dict[str, Any] = {"cwd": str(cwd), "mcpServers": []}
        if meta:
            params["_meta"] = meta
        bridge = self._connect()
        try:
            result = self._request(bridge, "session/new", params) or {}
            session_id = result.get("sessionId")
            if not session_id:
                raise GrokUnavailable(f"Grok's session/new answered without a session id: {result!r}")
            models = result.get("models") or {}
            current = models.get("currentModelId")
            if model and current and current != model:
                # Grok swaps an unknown model for its default without a word.
                self._request(bridge, "_x.ai/session/close", {"sessionId": session_id})
                offered = [m.get("modelId") for m in models.get("availableModels") or ()
                           if isinstance(m, dict) and m.get("modelId")]
                raise ValueError(f"Grok has no model {model!r} for {self.home}; it offers "
                                 f"{', '.join(offered) or current}")
            if yolo:
                _record_yolo(self.home, session_id)
            return str(session_id)
        finally:
            bridge.close()

    def prompt(self, session_id: str, cwd: str, text: str, *, wait: bool = False,
               timeout: float = 600) -> Optional[str]:
        """Send ``text`` as a user turn of ``session_id``.

        ``wait=False`` returns ``None`` as soon as the leader has taken the
        turn — running, or queued behind a running one — never after it
        ended; the leader runs it with no client attached. ``wait=True``
        returns the model's last message of that turn. A dormant session is
        loaded first (with yolo again if mnemo created it so). Raises
        :class:`SessionNotFound` when the session is in neither the leader nor
        the home, ``TimeoutError`` when ``timeout`` passes first (the turn
        keeps going in the leader).
        """
        bridge = self._connect()
        try:
            deadline = time.monotonic() + timeout
            try:
                return self._prompt_once(bridge, session_id, text, wait, deadline)
            except AcpError as exc:
                if "unknown session" not in str(exc.data or exc.error_message).lower():
                    raise
            self._load(bridge, session_id, cwd)
            return self._prompt_once(bridge, session_id, text, wait, deadline)
        finally:
            bridge.close()

    def _load(self, bridge: _Bridge, session_id: str, cwd: str) -> None:
        params: Dict[str, Any] = {"sessionId": session_id, "cwd": str(cwd), "mcpServers": []}
        if was_created_yolo(self.home, session_id):
            params["_meta"] = {"yoloMode": True}
        try:
            self._request(bridge, "session/load", params)
        except AcpError as exc:
            raise SessionNotFound(
                f"no Grok session {session_id} in {self.home} for {cwd}: {exc}") from exc

    def _prompt_once(self, bridge: _Bridge, session_id: str, text: str, wait: bool,
                     deadline: float) -> Optional[str]:
        turn = _Turn(session_id, str(uuid.uuid4()))
        rid = bridge.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
            "_meta": {"promptId": turn.prompt_id},
        })
        while True:
            msg = bridge.next_message(deadline)
            if msg is None:
                raise GrokUnavailable(
                    f"Grok's leader bridge exited during session/prompt: {bridge.stderr_tail() or 'no output'}")
            if not msg:
                what = "end its turn" if wait and turn.started else "take the turn"
                raise TimeoutError(f"Grok session {session_id} did not {what} in time")
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    error = msg["error"] or {}
                    if _is_auth_error(error):
                        raise GrokUnavailable(f"Grok home {self.home} is not logged in: run "
                                              f"`GROK_HOME={self.home} grok login`")
                    if "rate limit" in str(error.get("message") or "").lower():
                        raise RateLimited("session/prompt", error)
                    raise AcpError("session/prompt", error)
                return turn.text() if wait else None
            turn.see(msg)
            if turn.started and not wait:
                return None

    def roster(self) -> List[RosterEntry]:
        """Every session the leader knows: resident ones live, recent dormant ones from disk."""
        bridge = self._connect()
        try:
            result = _unwrap("_x.ai/sessions/list", self._request(bridge, "_x.ai/sessions/list", {}))
        finally:
            bridge.close()
        rows = (result or {}).get("sessions") if isinstance(result, dict) else None
        return [entry for entry in (self._entry(row) for row in rows or ()) if entry is not None]

    def _entry(self, row: Any) -> Optional[RosterEntry]:
        if not isinstance(row, dict) or not row.get("sessionId"):
            return None
        changed = row.get("lastChangeUnixMs")
        return RosterEntry(
            session_id=str(row["sessionId"]),
            cwd=str(row.get("cwd") or ""),
            activity=str(row.get("activity") or ""),
            resident=bool(row.get("resident")),
            model_id=row.get("modelId") or None,
            effort=row.get("reasoningEffort") or None,
            title=row.get("title") or None,
            last_change_ms=int(changed) if isinstance(changed, (int, float)) else None,
            home=self.home,
        )

    def close(self, session_id: str) -> str:
        """Close ``session_id`` in the leader; Grok's outcome: ``closed``, ``notResident``, ``superseded``."""
        bridge = self._connect()
        try:
            result = _unwrap("_x.ai/session/close",
                             self._request(bridge, "_x.ai/session/close", {"sessionId": session_id}))
        finally:
            bridge.close()
        if isinstance(result, dict) and result.get("outcome"):
            return str(result["outcome"])
        return "closed" if isinstance(result, dict) and result.get("success") else str(result)

    def billing(self) -> Dict[str, Any]:
        """``_x.ai/billing`` for this home's account, as Grok returns it."""
        bridge = self._connect()
        try:
            result = _unwrap("_x.ai/billing", self._request(bridge, "_x.ai/billing", {}))
        finally:
            bridge.close()
        return result if isinstance(result, dict) else {"result": result}


# --------------------------------------------------------------------------
# Reading sessions on disk (no leader needed)


def find_session(session_id: str, home: Path) -> Optional[Path]:
    """The directory of ``session_id`` (full id, or ``paths.short_id``) under ``home``."""
    found = paths.find_session_dir(session_id, home)
    if found is not None:
        return found
    wanted = session_id.replace("-", "").lower()
    if len(wanted) != 8:
        return None
    matches: List[Path] = []
    try:
        for parent in paths.sessions_root(home).iterdir():
            if not parent.is_dir():
                continue
            for child in parent.iterdir():
                if child.is_dir() and paths.short_id(child.name) == wanted:
                    matches.append(child)
    except OSError:
        return None
    return matches[0] if len(matches) == 1 else None


def disk_roster(home: Path) -> List[RosterEntry]:
    """The roster of ``home`` when its leader is down, read from its session files.

    No file says a session is running (spec, "Sessions on disk"), and with no
    leader none is resident in one, so every row is ``dormant`` and not
    ``resident``: what the leader itself reports for a session it holds only
    on disk. ``headless`` (``grok -p``) sessions are left out, as the leader
    leaves them out. Newest first. Never raises; starts nothing.
    """
    rows: List[Tuple[float, RosterEntry]] = []
    try:
        parents = [p for p in paths.sessions_root(home).iterdir() if p.is_dir()]
    except OSError:
        return []
    for parent in parents:
        try:
            children = [c for c in parent.iterdir() if c.is_dir()]
        except OSError:
            continue
        for child in children:
            try:
                summary = json.loads((child / "summary.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(summary, dict) or summary.get("session_kind") == "headless":
                continue
            info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
            changed = _unix_ms(summary.get("updated_at") or summary.get("last_active_at"))
            rows.append((changed or 0, RosterEntry(
                session_id=str(info.get("id") or child.name),
                cwd=str(info.get("cwd") or paths.decode_cwd_dir(parent) or ""),
                activity="dormant",
                resident=False,
                model_id=_text(summary.get("current_model_id")),
                effort=_text(summary.get("reasoning_effort")),
                title=_text(summary.get("generated_title")),
                last_change_ms=changed,
                home=home,
            )))
    rows.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _changed, entry in rows]


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _unix_ms(value: Any) -> Optional[int]:
    """A Grok RFC 3339 time as Unix milliseconds, the roster's ``lastChangeUnixMs``."""
    if not isinstance(value, str) or not value:
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$",
                     value.strip())
    if not match:
        return None
    zone = match.group(3) or "Z"
    try:
        stamp = datetime.fromisoformat(match.group(1) + ("+00:00" if zone == "Z" else zone))
    except ValueError:
        return None
    millis = int((match.group(2) or "0")[:3].ljust(3, "0"))
    return int(stamp.timestamp()) * 1000 + millis


def session_cwd(session_dir: Path) -> Optional[str]:
    """The cwd a session directory belongs to: its ``summary.json``, else its parent's name."""
    try:
        summary = json.loads((session_dir / "summary.json").read_text(encoding="utf-8"))
        cwd = (summary.get("info") or {}).get("cwd")
        if cwd:
            return str(cwd)
    except (OSError, ValueError, AttributeError):
        pass
    return paths.decode_cwd_dir(session_dir.parent)
