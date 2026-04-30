"""Thin wrapper around the ``gh`` CLI for self-fix PR operations.

All functions return ``None`` / ``False`` when ``gh`` is unavailable or
the underlying command fails — callers must handle the None case.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional


def create_branch(branch_name: str, *, repo_root: Path) -> Optional[str]:
    """Create (and checkout) a new git branch from the current HEAD.

    Returns the branch name on success, ``None`` on failure.
    """
    try:
        result = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return branch_name


def push_branch(branch_name: str, *, repo_root: Path) -> bool:
    """Push *branch_name* to ``origin``.

    Returns ``True`` on success, ``False`` on failure.
    """
    try:
        result = subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def open_pr(
    *,
    branch: str,
    title: str,
    body: str,
    labels: List[str],
    draft: bool,
    repo_root: Path,
) -> Optional[int]:
    """Open a GitHub pull request and return its number.

    Returns ``None`` when ``gh`` is unavailable, the command fails, or the
    output cannot be parsed as an integer.
    """
    cmd = [
        "gh", "pr", "create",
        "--base", "master",
        "--head", branch,
        "--title", title,
        "--body", body,
    ]
    for label in labels:
        cmd += ["--label", label]
    if draft:
        cmd.append("--draft")
    # Request only the PR number in the output
    cmd += ["--json", "number", "--jq", ".number"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except (ValueError, AttributeError):
        return None
