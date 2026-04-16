"""Shared log-rotation utility for JSONL files."""
from __future__ import annotations

import os
from pathlib import Path


def rotate_if_needed(log_path: Path, max_bytes: int) -> None:
    """Rotate log_path → log_path.1 if it exceeds max_bytes. Never raises."""
    try:
        if log_path.exists() and log_path.stat().st_size > max_bytes:
            rotated = log_path.with_suffix(log_path.suffix + ".1")
            os.rename(log_path, rotated)
    except OSError:
        pass
