"""Best-effort error logging with circuit breaker."""
from __future__ import annotations

import json
import traceback
from datetime import datetime, timedelta
from pathlib import Path

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
                    entry = json.loads(raw.decode("utf-8"))
                    ts = datetime.fromisoformat(entry["timestamp"])
                    where = entry.get("where", "")
                    if where.startswith("extract."):
                        continue
                    if ts >= cutoff:
                        recent += 1
                except Exception:
                    continue
                if recent > THRESHOLD_PER_HOUR:
                    return False
        return recent <= THRESHOLD_PER_HOUR
    except Exception:
        return True  # fail-open: never block hooks because the breaker is broken


def reset(vault_root: Path) -> None:
    log_path = _log_path(vault_root)
    if not log_path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    try:
        log_path.rename(log_path.with_name(f"{ERROR_LOG_NAME}.{stamp}"))
    except OSError:
        pass
