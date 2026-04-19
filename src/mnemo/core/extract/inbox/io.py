"""Atomic write + content hash helpers for the extraction inbox.

D3/D4 consolidation target (v0.9 PR I): all sites that previously wrote
their own ``"sha256:" + hashlib.sha256(...).hexdigest()`` one-liner or
inlined the temp-file-then-os.replace dance now route through
``content_hash`` and ``atomic_write``.

The legacy underscore-prefixed names (``_atomic_write`` / ``_file_hash``)
are kept as deprecated re-exports for v0.8 callers; both emit a
``DeprecationWarning`` and are scheduled for removal in v0.10.
"""
from __future__ import annotations

import hashlib
import os
import warnings
from pathlib import Path
from typing import Union

from mnemo.core.extract.inbox.types import ExtractionIOError


def content_hash(source: Union[Path, str, bytes]) -> str:
    """Return the ``sha256:<hex>`` digest of *source*.

    Accepts a :class:`Path` (digest of the file's bytes), a ``str``
    (digest of its UTF-8 encoding — the rendered-page case), or raw
    ``bytes``. Single helper covers both the on-disk and in-memory
    cases that previously each spelled the SHA256 prefix inline
    (D3 consolidation, v0.9 PR I).
    """
    if isinstance(source, Path):
        data = source.read_bytes()
    elif isinstance(source, str):
        data = source.encode("utf-8")
    else:
        data = source
    return "sha256:" + hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via a sibling .tmp + os.replace.

    Creates parent directories on demand. Cleans up the temp file on
    failure and raises :class:`ExtractionIOError` so callers can
    distinguish extraction-write failures from arbitrary OSErrors.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content.encode("utf-8"))
        os.replace(tmp, path)
    except OSError as exc:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise ExtractionIOError(f"failed to write {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Deprecated underscore-prefixed aliases — kept so v0.8 importers keep
# working. Scheduled for removal in v0.10. New code MUST use ``atomic_write``
# / ``content_hash``.
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    warnings.warn(
        "`_atomic_write` is deprecated in v0.9; use `atomic_write`. "
        "This alias will be removed in v0.10.",
        DeprecationWarning,
        stacklevel=2,
    )
    atomic_write(path, content)


def _file_hash(path: Path) -> str:
    warnings.warn(
        "`_file_hash` is deprecated in v0.9; use `content_hash`. "
        "This alias will be removed in v0.10.",
        DeprecationWarning,
        stacklevel=2,
    )
    return content_hash(path)
