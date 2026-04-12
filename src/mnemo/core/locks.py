"""Cross-platform advisory lock built on os.mkdir atomicity."""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Iterator


@contextlib.contextmanager
def try_lock(lock_dir: Path, stale_after: float = 60.0) -> Iterator[bool]:
    """Non-blocking advisory lock. Yields True if held, False otherwise.

    Uses os.mkdir as the atomic primitive — works identically on POSIX and
    Windows with no OS-specific imports. Reclaims the lock if the directory
    is older than `stale_after` seconds.
    """
    lock_dir = Path(lock_dir)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    held = False
    try:
        try:
            os.mkdir(lock_dir)
            held = True
        except FileExistsError:
            try:
                age = time.time() - lock_dir.stat().st_mtime
            except OSError:
                age = 0.0
            if age > stale_after:
                try:
                    os.rmdir(lock_dir)
                    os.mkdir(lock_dir)
                    held = True
                except OSError:
                    held = False
        yield held
    finally:
        if held:
            try:
                os.rmdir(lock_dir)
            except OSError:
                pass
