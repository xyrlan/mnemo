# src/mnemo/core/log_writer.py
"""Atomic single-syscall append to daily log."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import IO, Any

from mnemo.core import paths

MAX_LINE_BYTES = 3800  # Linux PIPE_BUF=4096 safety margin


def _flock_ex(fh: IO[bytes]) -> None:
    """Acquire an exclusive advisory lock on a file handle (POSIX only).

    No-op on Windows and unsupported filesystems. Released when the file
    handle is closed. Used to harden append_line against rare O_APPEND
    races on overlayfs (GitHub Actions, Docker) where separate-fd writers
    can lose entries.
    """
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except (ImportError, OSError):
        pass


def _header(agent: str) -> bytes:
    today = date.today().isoformat()
    text = (
        "---\n"
        f"tags: [log, {agent}]\n"
        f"date: {today}\n"
        "---\n"
        f"# {today} — {agent}\n"
        "\n"
    )
    return text.encode("utf-8")


def _format_line(content: str) -> bytes:
    now = datetime.now().strftime("%H:%M")
    line = f"- **{now}** — {content}\n"
    encoded = line.encode("utf-8")
    if len(encoded) > MAX_LINE_BYTES:
        encoded = encoded[: MAX_LINE_BYTES - 5] + b"...\n"
    return encoded


def append_line(agent: str, content: str, cfg: dict[str, Any]) -> None:
    log_path = paths.today_log(cfg, agent)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _format_line(content)
    try:
        with open(log_path, "xb", buffering=0) as fh:
            _flock_ex(fh)
            fh.write(_header(agent))
    except FileExistsError:
        pass
    with open(log_path, "ab", buffering=0) as fh:
        _flock_ex(fh)
        fh.write(payload)
