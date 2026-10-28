"""Atomic file write, safe under concurrent writers.

This is the mkstemp variant of tmp-then-replace that :mod:`mnemo.core.session`
arrived at after the 2026-07-31 session-cache crash, extracted so index
writers stop re-deriving the broken fixed-name version: a shared
``<target>.tmp`` name lets one process' ``os.replace`` consume the other's
tmp file, and the loser raises FileNotFoundError — observed four times in two
weeks on ``reflex-index.json`` when concurrent SessionStarts rebuilt at once.

Deliberately imports nothing from mnemo so any module can use it without
circular-import risk (the reason rule_activation once inlined its own copy).
"""
from __future__ import annotations

import os
import tempfile
import time
import uuid
from pathlib import Path

# Windows os.replace can lose several races to a concurrent writer before
# winning, so a couple of retries is not enough (see core/session.py).
_ATTEMPTS = 5


def previous_path(path: Path) -> Path:
    """Where :func:`atomic_write_bytes` keeps the version a write replaced."""
    path = Path(path)
    return path.with_name(path.name + ".prev")


def _keep_previous(path: Path) -> None:
    """Hard-link ``path`` as :func:`previous_path` before it is replaced.

    A link, not a copy: the replace only drops the target's name, so the old
    bytes live on under ``.prev`` at no cost however large the file is. A
    target that is empty or starts with NUL is what a crash leaves (#735) and
    is not kept, so a rebuild after one does not overwrite the last good
    version with it. Best-effort: a filesystem without hard links keeps none.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(1)
    except OSError:
        return
    if head in (b"", b"\x00"):
        return
    prev = previous_path(path)
    staged = prev.with_name(f"{prev.name}.{uuid.uuid4().hex[:12]}.tmp")
    try:
        os.link(path, staged)
        os.replace(staged, prev)
    except OSError:
        try:
            os.unlink(staged)
        except OSError:
            pass


def atomic_write_bytes(
    path: Path, data: bytes, *, durable: bool = False, keep_previous: bool = False
) -> None:
    """Write ``data`` to ``path`` atomically; each call stages through its own
    mkstemp tmp file so concurrent writers to the same target never collide.

    ``durable`` flushes the bytes to disk before the rename. Without it a
    power loss on NTFS can keep the rename and lose the bytes: the target
    comes back the new size and all NUL (#735). It costs a disk flush, so it
    is for a file a crash must not zero, not for every hook's cache.

    ``keep_previous`` keeps the version being replaced at
    :func:`previous_path`, for a reader to fall back to when the target is
    unreadable (#767).

    Retries transient races (dir vanishing mid-write, Windows sharing
    violations). If every attempt loses but a peer has meanwhile populated the
    target, that peer's write is a complete file of the same kind — treated as
    success rather than surfacing a transient error to a hook path.
    """
    path = Path(path)
    if keep_previous:
        _keep_previous(path)
    last_exc: OSError | None = None
    for attempt in range(_ATTEMPTS):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f"{path.stem}.", suffix=path.suffix + ".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                if durable:
                    fh.flush()
                    os.fsync(fh.fileno())
            os.replace(tmp_name, path)
            return
        except (FileNotFoundError, PermissionError) as exc:
            last_exc = exc
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            # Stagger retries so racing writers don't collide on every attempt.
            if attempt < _ATTEMPTS - 1:
                time.sleep(0.002 * (attempt + 1))
    if path.exists():
        return
    assert last_exc is not None
    raise last_exc
