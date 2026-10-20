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
from pathlib import Path

# Windows os.replace can lose several races to a concurrent writer before
# winning, so a couple of retries is not enough (see core/session.py).
_ATTEMPTS = 5


def atomic_write_bytes(path: Path, data: bytes, *, durable: bool = False) -> None:
    """Write ``data`` to ``path`` atomically; each call stages through its own
    mkstemp tmp file so concurrent writers to the same target never collide.

    ``durable`` flushes the bytes to disk before the rename. Without it a
    power loss on NTFS can keep the rename and lose the bytes: the target
    comes back the new size and all NUL (#735). It costs a disk flush, so it
    is for a file a crash must not zero, not for every hook's cache.

    Retries transient races (dir vanishing mid-write, Windows sharing
    violations). If every attempt loses but a peer has meanwhile populated the
    target, that peer's write is a complete file of the same kind — treated as
    success rather than surfacing a transient error to a hook path.
    """
    path = Path(path)
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
