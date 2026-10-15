"""Cross-platform advisory lock built on os.mkdir atomicity."""
from __future__ import annotations

import contextlib
import os
import sys
import time
import uuid
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


_PROCESS_OWNER = ".owner"


def _guard_path(lock_dir: Path) -> Path:
    return lock_dir.with_name(f".{lock_dir.name}.guard")


@contextlib.contextmanager
def _try_guard(path: Path) -> Iterator[bool]:
    """Try to hold a stable OS lock file, released automatically on exit."""
    try:
        handle = path.open("a+b")
    except OSError:
        yield False
        return

    acquired = False
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError):
            pass
        else:
            acquired = True
        yield acquired
    finally:
        if acquired:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
        handle.close()


def _claim_directory(lock_dir: Path, stale_after: float) -> bool:
    """Create the marker directory, reclaiming only our marker or an old legacy one."""
    if lock_dir.exists():
        owner = lock_dir / _PROCESS_OWNER
        try:
            if owner.is_file() and not owner.is_symlink():
                entries = list(lock_dir.iterdir())
                if len(entries) != 1 or entries[0].name != _PROCESS_OWNER:
                    return False
                owner.unlink()
                os.rmdir(lock_dir)
            else:
                age = time.time() - lock_dir.stat().st_mtime
                if age <= stale_after:
                    return False
                os.rmdir(lock_dir)
        except OSError:
            return False

    try:
        os.mkdir(lock_dir)
        return True
    except OSError:
        return False


@contextlib.contextmanager
def try_process_lock(lock_dir: Path, stale_after: float = 60.0) -> Iterator[bool]:
    """Lock a long-running operation with an OS-released guard.

    The directory remains as a status marker. Existing empty lock directories
    are reclaimed after the legacy age threshold; marker directories from a
    dead process are reclaimed as soon as the OS guard becomes available.
    """
    lock_dir = Path(lock_dir)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    guard_path = _guard_path(lock_dir)
    owner_path = lock_dir / _PROCESS_OWNER
    token = uuid.uuid4().hex

    with _try_guard(guard_path) as guard_held:
        if not guard_held or not _claim_directory(lock_dir, stale_after):
            yield False
            return

        try:
            owner_path.write_text(token, encoding="ascii")
        except OSError:
            try:
                os.rmdir(lock_dir)
            except OSError:
                pass
            yield False
            return

        try:
            yield True
        finally:
            try:
                if owner_path.read_text(encoding="ascii") == token:
                    owner_path.unlink()
                    os.rmdir(lock_dir)
            except (OSError, UnicodeError):
                pass


def process_lock_held(lock_dir: Path, stale_after: float = 60.0) -> bool:
    """Whether a process lock or a recent legacy lock directory is held."""
    lock_dir = Path(lock_dir)
    guard_path = _guard_path(lock_dir)
    if not lock_dir.exists() and not guard_path.exists():
        return False

    with _try_guard(guard_path) as guard_held:
        if not guard_held:
            return True
        if not lock_dir.exists():
            return False
        if (lock_dir / _PROCESS_OWNER).exists():
            return False
        try:
            return time.time() - lock_dir.stat().st_mtime <= stale_after
        except OSError:
            return True
