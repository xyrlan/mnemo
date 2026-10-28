"""Best-effort error logging with circuit breaker."""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ERROR_LOG_NAME = ".errors.log"
ROTATE_BYTES = 5 * 1024 * 1024
THRESHOLD_PER_HOUR = 10


def _log_path(vault_root: Path) -> Path:
    return Path(vault_root) / ERROR_LOG_NAME


def _rotate_if_needed(log_path: Path) -> None:
    try:
        if log_path.exists() and log_path.stat().st_size > ROTATE_BYTES:
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            log_path.rename(log_path.with_name(f"{ERROR_LOG_NAME}.{stamp}"))
    except OSError:
        pass


def log_error(vault_root: Path, where: str, exc: BaseException) -> None:
    """Append a JSON line. Never raises."""
    try:
        log_path = _log_path(vault_root)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(log_path)
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "where": where,
            "kind": type(exc).__name__,
            "message": str(exc),
            "traceback_summary": traceback.format_exception_only(type(exc), exc)[-1].strip(),
        }
        line = json.dumps(entry) + "\n"
        with open(log_path, "ab", buffering=0) as fh:
            fh.write(line.encode("utf-8"))
    except Exception:
        return  # never propagate


def _breaker_relevant(entry: dict, cutoff: datetime) -> bool:
    """True when a log entry counts toward the breaker.

    Background extraction, the session-end scheduler and undelivered child
    reports are excluded: their failures are already reported elsewhere and
    must not silence the hooks.
    Raises on a malformed entry; callers skip those.
    """
    where = entry.get("where", "")
    if where.startswith("extract.") or where.startswith("session_end.schedule"):
        return False
    # A child's report to a parent the maintainer closed (#454) is logged so
    # it is never silent; it is not a hook failing.
    if where == "child_report.undelivered":
        return False
    # An automatic wake that failed or was skipped (#667) is the same: a
    # record of a child, bounded per window, not a hook failing.
    if where.startswith("rewake."):
        return False
    # A child that could not be lowered below normal CPU (#723) still runs,
    # just at the user's priority; that must not pause the hooks too.
    if where.startswith("child_priority."):
        return False
    return datetime.fromisoformat(entry["timestamp"]) >= cutoff


def _strike(entry: dict) -> tuple[str, str, str]:
    """The breaker's unit: one ``(where, kind)`` per wall-clock minute.

    A single command that loops and logs once per item writes a burst of
    rows sharing a timestamp. That is one failure, not many: on 2026-09-15 one
    ``mnemo sessions --consume-unblocks`` pass wrote 27 same-second
    ``unblocks.consume`` rows and paused every hook for an hour (#314). A hook
    failing on every call still trips it — every minute it keeps failing is a
    fresh strike.
    """
    minute = entry["timestamp"][:16]  # "YYYY-MM-DDTHH:MM", as log_error writes it
    return entry.get("where", ""), entry.get("kind", ""), minute


def _recent_entries(vault_root: Path):
    """Well-formed log entries the breaker counts, oldest first."""
    log_path = _log_path(vault_root)
    if not log_path.exists():
        return
    cutoff = datetime.now() - timedelta(hours=1)
    with open(log_path, "rb") as fh:
        for raw in fh:
            try:
                entry = json.loads(raw.decode("utf-8"))
                if not _breaker_relevant(entry, cutoff):
                    continue
                _strike(entry)
            except Exception:
                continue
            yield entry


def recent_strikes(vault_root: Path) -> int:
    """Distinct ``(where, kind, minute)`` strikes in the last hour; fail-open to 0."""
    try:
        return len({_strike(e) for e in _recent_entries(vault_root)})
    except Exception:
        return 0


def should_run(vault_root: Path) -> bool:
    """Return False if circuit breaker is open: more than
    :data:`THRESHOLD_PER_HOUR` strikes (see :func:`_strike`) in the last hour."""
    try:
        strikes: set = set()
        for entry in _recent_entries(vault_root):
            strikes.add(_strike(entry))
            if len(strikes) > THRESHOLD_PER_HOUR:
                return False
        return True
    except Exception:
        return True  # fail-open: never block hooks because the breaker is broken


def recent_summary(vault_root: Path) -> tuple[int, list[tuple[str, int]]]:
    """(error rows counted by the breaker in the last hour, ``where`` buckets by count desc).

    Same exclusions as :func:`should_run`, but counts rows, not strikes: the
    rows are what a human finds in the log. Fail-open to ``(0, [])``.
    """
    try:
        buckets: dict[str, int] = {}
        for entry in _recent_entries(vault_root):
            where = entry.get("where", "?")
            buckets[where] = buckets.get(where, 0) + 1
        ordered = sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0]))
        return sum(buckets.values()), ordered
    except Exception:
        return 0, []


def remedy_line(vault_root: Path) -> str:
    """One sentence for status/doctor/session-start when the breaker is open."""
    count, buckets = recent_summary(vault_root)
    top = f", most from {buckets[0][0]}" if buckets else ""
    return (
        f"circuit breaker open ({count} errors in the last hour{top}). "
        "Hooks are off until it cools down (1h) — run `mnemo fix` to reset now, "
        "`mnemo doctor` to see the errors."
    )


def reset(vault_root: Path) -> None:
    log_path = _log_path(vault_root)
    if not log_path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    try:
        log_path.rename(log_path.with_name(f"{ERROR_LOG_NAME}.{stamp}"))
    except OSError:
        pass


def load_validated_json(
    path: Path,
    expected_schema_version: Any,
    *,
    vault_root: Path,
    error_namespace: str,
    fallback: Path | None = None,
) -> dict | None:
    """Load a JSON dict from ``path`` with discriminating error handling.

    Returns ``None`` for every failure path; never raises. Used by both
    ``rule_activation.load_index`` and ``reflex.index.load_index`` — the
    duplicated 25-line try/except dance is now here.

    Error-logging policy:
      - Missing file → silent (first run, expected).
      - Read error (OSError other than FileNotFoundError) → logged under
        ``<error_namespace>.read``. A ``PermissionError`` is retried first:
        see :data:`_LOCKED_READ_ATTEMPTS`.
      - Decode / parse error → logged under ``<error_namespace>.parse``.
      - Root is not a dict → silent (hand-authored malformed file; the
        caller's ``rebuild`` path handles recovery).
      - ``schema_version`` mismatch → silent (post-upgrade, expected).

    ``expected_schema_version`` may be a tuple of the versions a reader still
    accepts, for a format whose old version it can read until a rebuild.

    ``fallback`` is read the same way when ``path`` exists but cannot be
    read, parsed, or is not a dict — the empty or NUL-filled file a crash
    leaves behind an atomic write (#767), or a file still locked after the
    retries. Its own failures log under ``<error_namespace>.fallback``. A
    missing ``path`` or a schema mismatch does not fall back: neither is
    damage.
    """
    data = _read_json(path, vault_root, error_namespace)
    if data is _UNREADABLE and fallback is not None:
        data = _read_json(fallback, vault_root, f"{error_namespace}.fallback")
    if not isinstance(data, dict):
        return None
    accepted = (expected_schema_version if isinstance(expected_schema_version, tuple)
                else (expected_schema_version,))
    if data.get("schema_version") not in accepted:
        return None
    return data


_UNREADABLE = object()

# On Windows a reader that opens a file while a writer's ``os.replace`` moves
# a new version over it gets a sharing violation, PermissionError, for the
# moment the rename holds the file. The kept ``.prev`` is no refuge: the next
# write replaces it the same way, so a writer rebuilding back to back (or a
# second SessionStart rebuilding at once) can hold both, and a reader that
# lost the live file to one rename lost ``.prev`` to the next
# (``test_readers_during_rebuilds_always_get_an_index`` on Windows CI, #767).
# The lock lasts as long as a rename, so it is waited out, staggered as the
# writer staggers its own retries (core/atomic.py), before the file counts as
# unreadable. It also means a race reads the new version, not the one before.
_LOCKED_READ_ATTEMPTS = 5


def _read_json(path: Path, vault_root: Path, error_namespace: str) -> Any:
    """The parsed JSON dict at ``path``, ``None`` when it does not exist, or
    ``_UNREADABLE`` when it exists and cannot be read as a dict."""
    for attempt in range(_LOCKED_READ_ATTEMPTS):
        try:
            raw_bytes = path.read_bytes()
            break
        except FileNotFoundError:
            return None
        except PermissionError as exc:
            if attempt == _LOCKED_READ_ATTEMPTS - 1:
                log_error(vault_root, f"{error_namespace}.read", exc)
                return _UNREADABLE
            time.sleep(0.002 * (attempt + 1))
        except OSError as exc:
            log_error(vault_root, f"{error_namespace}.read", exc)
            return _UNREADABLE
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        log_error(vault_root, f"{error_namespace}.parse", exc)
        return _UNREADABLE
    if not isinstance(data, dict):
        return _UNREADABLE
    return data
