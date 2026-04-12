# src/mnemo/core/agent.py
"""Git repo detection and agent naming."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentInfo:
    name: str
    repo_root: str
    has_git: bool


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(name: str) -> str:
    cleaned = _SAFE_NAME.sub("-", name).strip("-")
    return cleaned or "root"


def _find_git_root(start: Path) -> Path | None:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def resolve_agent(cwd: str) -> AgentInfo:
    start = Path(cwd) if cwd else Path.cwd()
    git_root = _find_git_root(start)
    if git_root is not None:
        return AgentInfo(name=_sanitize(git_root.name), repo_root=str(git_root), has_git=True)
    base = start.resolve()
    return AgentInfo(name=_sanitize(base.name), repo_root=str(base), has_git=False)
