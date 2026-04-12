# src/mnemo/core/mirror.py
"""Claude → vault sync. rsync preferred, pure-Python fallback."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from mnemo.core import agent, locks, paths


def _claude_projects_root() -> Path:
    return Path(os.path.expanduser("~/.claude/projects"))


def _decode_path(parts: list[str], cur: Path | None = None) -> Path | None:
    """Reverse a Claude Code dash-encoded project directory back to a real filesystem path.

    The encoded format is an absolute path with '/' replaced by '-'. Since '-' can also
    appear inside directory and repo names, the inverse is ambiguous. We resolve the
    ambiguity by walking the filesystem depth-first: at each level, try consuming 1, 2,
    3, ... remaining `parts` as the next directory name; if a candidate exists, recurse
    on the remainder. Returns the deepest fully-resolved path, or None if no decoding
    matches a real path.
    """
    if cur is None:
        cur = Path("/")
    if not parts:
        return cur
    for take in range(1, len(parts) + 1):
        candidate = cur / "-".join(parts[:take])
        try:
            exists = candidate.exists()
        except OSError:
            exists = False
        if exists:
            result = _decode_path(parts[take:], candidate)
            if result is not None:
                return result
    return None


def _agent_from_project_dir(name: str) -> str:
    cleaned = name.strip("-")
    if not cleaned:
        return "root"
    parts = cleaned.split("-")
    decoded = _decode_path(parts)
    if decoded is not None:
        return agent.resolve_agent(str(decoded)).name
    # Fallback for paths that no longer exist on disk: use the historical heuristic.
    tail = "-".join(parts[3:])
    if tail:
        return tail
    return parts[-1] or "root"


def _has_rsync() -> bool:
    return shutil.which("rsync") is not None


def _rsync_copy(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["rsync", "-a", f"{src}/", f"{dst}/"],
        check=False,
        capture_output=True,
    )


def _python_copy(src: Path, dst: Path) -> None:
    """Pure-Python rsync substitute. Never deletes from dst."""
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(Path(root) / f, target_dir / f)


def mirror_all(cfg: dict[str, Any]) -> None:
    vault = paths.vault_root(cfg)
    paths.bots_dir(cfg).mkdir(parents=True, exist_ok=True)
    projects_root = _claude_projects_root()
    if not projects_root.exists():
        return
    lock_dir = vault / ".mirror.lock"
    with locks.try_lock(lock_dir) as held:
        if not held:
            return
        for project_dir in projects_root.iterdir():
            memory_src = project_dir / "memory"
            if not memory_src.is_dir():
                continue
            agent = _agent_from_project_dir(project_dir.name)
            target = paths.memory_dir(cfg, agent)
            if _has_rsync():
                _rsync_copy(memory_src, target)
            else:
                _python_copy(memory_src, target)
