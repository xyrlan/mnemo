"""Snapshot of recall-cases.json so Tier 2 tuners cannot optimize against drift."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import IO

from mnemo.autopilot.core._dirs import (
    autopilot_dir,
    ensure_autopilot_dir,
    frozen_recall_path,
)


class FrozenSetMissing(RuntimeError):
    """Raised when callers expect a frozen recall set but none has been created."""


def _source_path(vault_root: Path) -> Path:
    return autopilot_dir(vault_root) / "recall-cases.json"


def frozen_path(*, vault_root: Path) -> Path:
    return frozen_recall_path(vault_root)


def freeze_current(*, vault_root: Path, force: bool = False) -> Path:
    src = _source_path(vault_root)
    if not src.exists():
        raise FileNotFoundError(f"no recall-cases.json at {src}")
    ensure_autopilot_dir(vault_root)
    dest = frozen_path(vault_root=vault_root)
    if dest.exists() and not force:
        return dest
    shutil.copyfile(src, dest)
    return dest


def load_frozen(*, vault_root: Path) -> IO[str]:
    p = frozen_path(vault_root=vault_root)
    if not p.exists():
        raise FrozenSetMissing(f"no frozen recall set at {p}")
    return p.open("r", encoding="utf-8")
