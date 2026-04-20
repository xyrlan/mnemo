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


def _read_gitdir_pointer(git_file: Path) -> Path | None:
    """Parse a `.git` file's `gitdir: <path>` line. Returns the resolved path or None."""
    try:
        text = git_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("gitdir:"):
            target = line[len("gitdir:"):].strip()
            if not target:
                return None
            target_path = Path(target)
            if not target_path.is_absolute():
                target_path = (git_file.parent / target_path).resolve()
            return target_path
    return None


def _resolve_common_dir(worktree_gitdir: Path) -> Path | None:
    """Given a worktree's gitdir (e.g. .git/worktrees/feature-x), return the main repo root.

    Reads `<worktree_gitdir>/commondir` (relative path back to the main .git dir),
    then returns its parent (which is the main repo root). Returns None on any failure.
    """
    commondir_file = worktree_gitdir / "commondir"
    if not commondir_file.is_file():
        return None
    try:
        rel = commondir_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not rel:
        return None
    common_git_dir = (worktree_gitdir / rel).resolve()
    # common_git_dir is the main repo's `.git` directory; its parent is the repo root.
    if common_git_dir.name == ".git":
        return common_git_dir.parent
    # Defensive: some setups point directly at the repo root.
    return common_git_dir


def resolve_canonical_agent(cwd: str) -> AgentInfo:
    """Like `resolve_agent`, but follows `.git` worktree pointers to the main repo.

    For a worktree at `~/proj-feature-x` whose `.git` file points back to
    `~/proj/.git/worktrees/feature-x`, returns AgentInfo(name="proj", ...).

    Falls back to `resolve_agent(cwd)` for: missing `.git`, malformed `.git` file,
    missing `commondir`, or any I/O error during resolution.
    """
    start = Path(cwd) if cwd else Path.cwd()
    git_root = _find_git_root(start)
    if git_root is None:
        return resolve_agent(cwd)
    git_marker = git_root / ".git"
    # Main repo: .git is a directory. Already canonical.
    if git_marker.is_dir():
        return AgentInfo(name=_sanitize(git_root.name), repo_root=str(git_root.resolve()), has_git=True)
    # Worktree: .git is a file pointing to the worktree's gitdir under the main repo.
    if git_marker.is_file():
        worktree_gitdir = _read_gitdir_pointer(git_marker)
        if worktree_gitdir is None:
            return AgentInfo(name=_sanitize(git_root.name), repo_root=str(git_root.resolve()), has_git=True)
        canonical_root = _resolve_common_dir(worktree_gitdir)
        if canonical_root is None:
            return AgentInfo(name=_sanitize(git_root.name), repo_root=str(git_root.resolve()), has_git=True)
        return AgentInfo(name=_sanitize(canonical_root.name), repo_root=str(canonical_root), has_git=True)
    # Should not happen — _find_git_root only returns dirs whose `.git` exists.
    return resolve_agent(cwd)
