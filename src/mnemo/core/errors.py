"""Best-effort error logging with circuit breaker."""
from __future__ import annotations

import json
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

    Background extraction and the session-end scheduler are excluded: their
    failures are already reported elsewhere and must not silence the hooks.
    Raises on a malformed entry; callers skip those.
    """
    where = entry.get("where", "")
    if where.startswith("extract.") or where.startswith("session_end.schedule"):
        return False
    return datetime.fromisoformat(entry["timestamp"]) >= cutoff


def should_run(vault_root: Path) -> bool:
    """Return False if circuit breaker is open."""
    try:
        log_path = _log_path(vault_root)
        if not log_path.exists():
            return True
        cutoff = datetime.now() - timedelta(hours=1)
        recent = 0
        with open(log_path, "rb") as fh:
            for raw in fh:
                try:
                    if _breaker_relevant(json.loads(raw.decode("utf-8")), cutoff):
                        recent += 1
                except Exception:
                    continue
                if recent > THRESHOLD_PER_HOUR:
                    return False
        return recent <= THRESHOLD_PER_HOUR
    except Exception:
        return True  # fail-open: never block hooks because the breaker is broken


def recent_summary(vault_root: Path) -> tuple[int, list[tuple[str, int]]]:
    """(errors counted by the breaker in the last hour, ``where`` buckets by count desc).

    Same exclusions as :func:`should_run`; fail-open to ``(0, [])``.
    """
    try:
        log_path = _log_path(vault_root)
        if not log_path.exists():
            return 0, []
        cutoff = datetime.now() - timedelta(hours=1)
        buckets: dict[str, int] = {}
        with open(log_path, "rb") as fh:
            for raw in fh:
                try:
                    entry = json.loads(raw.decode("utf-8"))
                    if _breaker_relevant(entry, cutoff):
                        where = entry.get("where", "?")
                        buckets[where] = buckets.get(where, 0) + 1
                except Exception:
                    continue
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
) -> dict | None:
    """Load a JSON dict from ``path`` with discriminating error handling.

    Returns ``None`` for every failure path; never raises. Used by both
    ``rule_activation.load_index`` and ``reflex.index.load_index`` — the
    duplicated 25-line try/except dance is now here.

    Error-logging policy:
      - Missing file → silent (first run, expected).
      - Read error (OSError other than FileNotFoundError) → logged under
        ``<error_namespace>.read``.
      - Decode / parse error → logged under ``<error_namespace>.parse``.
      - Root is not a dict → silent (hand-authored malformed file; the
        caller's ``rebuild`` path handles recovery).
      - ``schema_version`` mismatch → silent (post-upgrade, expected).
    """
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        log_error(vault_root, f"{error_namespace}.read", exc)
        return None
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        log_error(vault_root, f"{error_namespace}.parse", exc)
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != expected_schema_version:
        return None
    return data
