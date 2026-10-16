"""Where Grok Build keeps its state, as measured on grok 1.0.60 (2026-10-16).

Read from the Apache-2.0 source (``xai-grok-config/src/paths.rs``,
``xai-dirs/src/lib.rs``) and checked against a live session directory:

- The home is ``$GROK_HOME`` when non-empty, else ``~/.grok``. Everything is
  under it: ``auth.json``, ``config.toml``, ``sessions/``, ``leader.sock``.
  One home is one account; mnemo-desktop names extra accounts ``~/.grok-<slug>``.
- A session lives in ``<home>/sessions/<encoded cwd>/<session id>/``. The cwd
  is URL-encoded (``urlencoding::encode``: everything but ``A-Za-z0-9-._~``),
  which is ``urllib.parse.quote(cwd, safe="")``. When that is longer than 255
  bytes Grok uses ``<slug>-<blake3 hex16>`` instead and writes the original cwd
  to a ``.cwd`` file in the directory; mnemo never computes that form, it
  finds the directory by reading the ``.cwd`` files.
- The conversation the model saw is ``chat_history.jsonl`` in the session
  directory. A hook's ``transcript_path`` names ``updates.jsonl`` beside it.
- Session ids are UUIDv7: the leading hex is a timestamp, so two sessions
  started in the same second share their first eight characters. mnemo keys
  its ledgers by an 8-character short id; for Grok it is the *last* eight hex
  characters (:func:`short_id`).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Mapping, Optional
from urllib.parse import quote, unquote

HOME_ENV = "GROK_HOME"
LEADER_SOCKET_ENV = "GROK_LEADER_SOCKET"

#: Longest directory name Grok writes in URL-encoded form (``MAX_DIRNAME_BYTES``).
MAX_DIRNAME_BYTES = 255

CHAT_HISTORY = "chat_history.jsonl"
UPDATES = "updates.jsonl"
CWD_FILE = ".cwd"


def grok_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """The Grok home this process would use: ``$GROK_HOME``, else ``~/.grok``."""
    env = os.environ if env is None else env
    named = (env.get(HOME_ENV) or "").strip()
    if named:
        return Path(named).expanduser()
    return Path.home() / ".grok"


def grok_homes(env: Optional[Mapping[str, str]] = None) -> List[Path]:
    """Every Grok home on this machine that exists: one per account.

    ``$GROK_HOME`` first, then ``~/.grok``, then each ``~/.grok-<slug>`` (the
    accounts mnemo-desktop adds), deduplicated by resolved path.
    """
    candidates = [grok_home(env), Path.home() / ".grok"]
    try:
        candidates += sorted(p for p in Path.home().glob(".grok-*") if p.is_dir())
    except OSError:
        pass
    seen, out = set(), []
    for home in candidates:
        if not home.is_dir():
            continue
        key = os.path.normcase(os.path.realpath(str(home)))
        if key in seen:
            continue
        seen.add(key)
        out.append(home)
    return out


def sessions_root(home: Optional[Path] = None) -> Path:
    return (home if home is not None else grok_home()) / "sessions"


def encode_cwd(cwd: str) -> Optional[str]:
    """The directory name Grok gives ``cwd``, or ``None`` for the hashed form."""
    encoded = quote(cwd, safe="")
    if len(encoded.encode("utf-8")) <= MAX_DIRNAME_BYTES:
        return encoded
    return None


def decode_cwd_dir(directory: Path) -> Optional[str]:
    """The cwd a ``sessions/<name>`` directory belongs to, or ``None``."""
    decoded = unquote(directory.name)
    if decoded.startswith("/") or (len(decoded) > 1 and decoded[1] == ":"):
        return decoded
    try:
        return (directory / CWD_FILE).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def cwd_dir(cwd: str, home: Optional[Path] = None) -> Optional[Path]:
    """``<home>/sessions/<encoded cwd>`` for ``cwd`` if it exists, else ``None``."""
    root = sessions_root(home)
    encoded = encode_cwd(cwd)
    if encoded is not None:
        candidate = root / encoded
        return candidate if candidate.is_dir() else None
    try:
        for child in root.iterdir():
            if child.is_dir() and (child / CWD_FILE).is_file() and decode_cwd_dir(child) == cwd:
                return child
    except OSError:
        return None
    return None


def session_dir(cwd: str, session_id: str, home: Optional[Path] = None) -> Optional[Path]:
    """The directory of session ``session_id`` started in ``cwd``, if it exists."""
    parent = cwd_dir(cwd, home)
    if parent is None:
        return None
    candidate = parent / session_id
    return candidate if candidate.is_dir() else None


def find_session_dir(session_id: str, home: Optional[Path] = None) -> Optional[Path]:
    """The directory of ``session_id`` under any cwd of ``home``, if it exists."""
    try:
        for parent in sessions_root(home).iterdir():
            candidate = parent / session_id
            if candidate.is_dir():
                return candidate
    except OSError:
        return None
    return None


def chat_history_path(session_dir_path: Path) -> Path:
    """The conversation file inside a session directory."""
    return session_dir_path / CHAT_HISTORY


def short_id(session_id: str) -> str:
    """mnemo's 8-character key for a Grok session: its last eight hex digits."""
    return session_id.replace("-", "")[-8:].lower()


def leader_socket(home: Optional[Path] = None, env: Optional[Mapping[str, str]] = None) -> str:
    """The leader socket Grok would use: ``$GROK_LEADER_SOCKET``, else ``<home>/leader.sock``.

    macOS refuses a Unix socket path longer than 103 bytes (``SUN_LEN``); a
    leader under a long home fails to start with "path must be shorter than
    SUN_LEN" (measured 2026-10-16). This returns the path, it does not fix it.
    """
    env = os.environ if env is None else env
    named = (env.get(LEADER_SOCKET_ENV) or "").strip()
    if named:
        return named
    return str((home if home is not None else grok_home(env)) / "leader.sock")
