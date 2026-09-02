"""Durable record of which transcripts have been harvested.

Lives at ``<vault>/.mnemo/backfill-state.json``. Keyed by session id (the
transcript filename stem) with the file's content hash, so:

- a rerun is a no-op,
- an interrupted sweep resumes where it stopped,
- a transcript that grew on disk is harvested again,
- a transcript that fails three times is skipped for good.

Vault-wide fields sit beside the per-session map:

- ``installRunDone`` — a first-run sweep **completed**. Written by
  ``cmd_backfill`` on an install run that actually got to the end, never by
  the hook that launches it, so an environmental abort (missing ``claude``
  CLI, expired auth, rate limit) leaves the one-shot unspent.
- ``firstRunNoticeShown`` — the session-start invitation to run `mnemo
  backfill` has been shown once. A message, not a sweep: it never implies any
  transcript was read, and never spends ``installRunDone``.
- the spawn lock, a separate file — "a first-run sweep is in flight". See
  :func:`acquire_spawn_lock`.

Pure filesystem + hashing. No LLM dependency, so it tests without stubbing.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_ATTEMPTS = 3
_STATE_NAME = "backfill-state.json"
_LOCK_NAME = "backfill-install.lock"

# How long a spawn lock is believed before it is treated as abandoned. Sized
# against the work it guards, not against impatience: an install run is capped
# at ``installCap`` (20) sessions, each an LLM call that may retry after a
# timeout, so a legitimate sweep can run well over an hour. Stealing the lock
# from a sweep that is merely slow would double the user's spend, which is the
# thing the lock exists to prevent — so the TTL errs long. A sweep that ends
# normally, or aborts, releases the lock explicitly and never waits this out;
# only a hard kill does.
SPAWN_LOCK_TTL_SECONDS = 6 * 3600


def state_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / _STATE_NAME


def spawn_lock_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / _LOCK_NAME


def acquire_spawn_lock(vault_root: Path) -> bool:
    """Claim the right to launch an install run. True when this caller won.

    Two Claude Code windows opened in the same instant both read
    ``installRunDone`` as False and, without this, both spawn a sweep — twice
    ``installCap`` LLM calls on the user's account, and two children racing on
    ``save``'s shared temp file. ``O_CREAT | O_EXCL`` is the atomic test-and-set
    that a load/check/save cannot be.

    A lock older than :data:`SPAWN_LOCK_TTL_SECONDS` is assumed abandoned by a
    killed process and stolen, so a hard kill cannot wedge the feature forever.
    Stealing is deliberately not atomic against a *second* stealer arriving in
    the same microsecond at the six-hour boundary; that residual race costs one
    duplicate sweep in a case that requires two simultaneous session starts
    against an already-abandoned lock, and closing it would cost a real lock
    manager.
    """
    path = spawn_lock_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _claim(path):
        return True
    age = spawn_lock_age(vault_root)
    if age is None:
        return False  # it vanished under us; the next session can have it
    if age < SPAWN_LOCK_TTL_SECONDS:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return _claim(path)


def _claim(path: Path) -> bool:
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        os.write(fd, f"pid={os.getpid()} at={int(time.time())}\n".encode())
    finally:
        os.close(fd)
    return True


def release_spawn_lock(vault_root: Path) -> None:
    """Drop the lock. Safe to call when it is already gone."""
    try:
        spawn_lock_path(vault_root).unlink()
    except OSError:
        pass


def spawn_lock_age(vault_root: Path) -> float | None:
    """How far the lock's timestamp is from now, or None when there is no lock.

    Absolute distance, not signed elapsed time. A lock dated in the *future* —
    a backward clock jump, an NTP correction, a copied vault — otherwise
    produces a negative age that is forever below the TTL, so a lock left
    behind by a killed process could never be reaped and the feature would be
    wedged permanently. Reading the distance in either direction costs nothing
    and turns "impossible to reap" into "reaped once the skew exceeds the
    TTL", which is the safe end to fail towards.
    """
    try:
        return abs(time.time() - spawn_lock_path(vault_root).stat().st_mtime)
    except OSError:
        return None


def mark_install_run_done(vault_root: Path) -> None:
    """Record that a first-run sweep reached the end.

    Read-modify-write: the sweep it is reporting on just wrote the per-session
    records this shares a file with.
    """
    led = load(vault_root)
    led["installRunDone"] = True
    save(vault_root, led)


def notice_shown(led: dict[str, Any]) -> bool:
    """True when the first-run backfill invitation has already been shown."""
    return bool(led.get("firstRunNoticeShown", False))


def mark_notice_shown(vault_root: Path) -> None:
    """Record that the first-run invitation was shown. Read-modify-write.

    Separate from ``installRunDone``: the notice is a *message*, the marker is
    a *sweep*. Someone who reads the invitation and never runs `mnemo backfill`
    must not be pestered every session, and must not have their unspent sweep
    look spent to the CLI.
    """
    led = load(vault_root)
    led["firstRunNoticeShown"] = True
    save(vault_root, led)


def transcript_hash(path: Path) -> str:
    """Content hash of a transcript, or ``""`` when unreadable."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(65536), b""):
                h.update(block)
    except OSError:
        return ""
    return "sha256:" + h.hexdigest()


def load(vault_root: Path) -> dict[str, Any]:
    """Read the ledger. A missing or corrupt file yields a clean ledger."""
    path = state_path(vault_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        return {
            "schemaVersion": SCHEMA_VERSION,
            "sessions": {},
            "installRunDone": False,
            "firstRunNoticeShown": False,
        }
    data.setdefault("schemaVersion", SCHEMA_VERSION)
    data.setdefault("installRunDone", False)
    data.setdefault("firstRunNoticeShown", False)
    return data


def save(vault_root: Path, led: dict[str, Any]) -> None:
    """Atomically write the ledger."""
    path = state_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(led, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _entry(led: dict[str, Any], path: Path) -> dict[str, Any] | None:
    entry = led.get("sessions", {}).get(Path(path).stem)
    return entry if isinstance(entry, dict) else None


def should_harvest(led: dict[str, Any], path: Path) -> bool:
    """True when this transcript still needs work."""
    entry = _entry(led, path)
    if entry is None:
        return True
    if entry.get("hash") != transcript_hash(path):
        return True  # transcript changed on disk
    if entry.get("status") == "done":
        return False
    return int(entry.get("attempts") or 0) < MAX_ATTEMPTS


def attempts_exhausted(led: dict[str, Any], path: Path) -> bool:
    """True when this transcript is being skipped because it kept failing.

    ``should_harvest`` returns False for two unrelated populations — finished
    work and abandoned work — and a caller that reports "nothing to do" without
    telling them apart hides every failure the sweep ever had. This is the
    read-only predicate that separates them, so the key layout stays in this
    module rather than being reconstructed by the CLI.
    """
    entry = _entry(led, path)
    if entry is None or entry.get("status") == "done":
        return False
    if entry.get("hash") != transcript_hash(path):
        return False  # changed on disk — it gets a fresh budget
    return int(entry.get("attempts") or 0) >= MAX_ATTEMPTS


def clear_failed(led: dict[str, Any]) -> int:
    """Drop every failed entry so it can be attempted again. Returns the count.

    ``attempts >= MAX_ATTEMPTS`` is otherwise terminal: nothing decrements it,
    and the changed-on-disk escape hatch cannot fire for archived transcripts,
    which never change. That makes any misclassification of a failure
    permanent and hand-editable only. This is the blast-radius limiter — it
    matters more than getting every classification right, because it is what
    makes getting one wrong survivable.

    Entries are removed rather than reset, so a retried transcript is
    indistinguishable from one never seen. ``done`` entries are untouched: this
    retries failures, it does not re-harvest finished work.
    """
    sessions = led.get("sessions")
    if not isinstance(sessions, dict):
        return 0
    doomed = [
        key for key, entry in sessions.items()
        if isinstance(entry, dict) and entry.get("status") != "done"
    ]
    for key in doomed:
        del sessions[key]
    return len(doomed)


def mark_done(led: dict[str, Any], path: Path, *, produced: int) -> None:
    """Record success, resetting the attempt count to 0."""
    led.setdefault("sessions", {})[Path(path).stem] = {
        "status": "done",
        "hash": transcript_hash(path),
        "produced": int(produced),
        "attempts": 0,
    }


def mark_failed(led: dict[str, Any], path: Path, reason: str) -> None:
    """Record failure, re-hashing the file while carrying the attempt count forward."""
    key = Path(path).stem
    sessions = led.setdefault("sessions", {})
    prior = sessions.get(key) if isinstance(sessions.get(key), dict) else {}
    attempts = int(prior.get("attempts") or 0) + 1
    sessions[key] = {
        "status": "failed",
        "hash": transcript_hash(path),
        "attempts": attempts,
        "lastError": str(reason)[:200],
    }
