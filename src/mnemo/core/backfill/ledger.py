"""Durable record of which transcripts have been harvested.

Lives at ``<vault>/.mnemo/backfill-state.json``. Keyed by session id (the
transcript filename stem) with the file's content hash, so:

- a rerun is a no-op,
- an interrupted sweep resumes where it stopped,
- a transcript that grew on disk is harvested again,
- a transcript that fails three times is skipped for good.

Pure filesystem + hashing. No LLM dependency, so it tests without stubbing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_ATTEMPTS = 3
_STATE_NAME = "backfill-state.json"


def state_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / _STATE_NAME


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
        return {"schemaVersion": SCHEMA_VERSION, "sessions": {}, "installRunDone": False}
    data.setdefault("schemaVersion", SCHEMA_VERSION)
    data.setdefault("installRunDone", False)
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
