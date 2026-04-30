"""Thin git wrapper for Tier 3 signal collection.

All subprocess calls use ``capture_output=True, text=True`` and swallow
``FileNotFoundError`` so callers receive empty strings/lists rather than
exceptions when git is absent or cwd is not inside a repo.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List


def _run(args: list[str], cwd: Path) -> str:
    """Run a git command and return stdout; return '' on any error."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            cwd=str(cwd),
        )
        if result.returncode == 0:
            return result.stdout
        return ""
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def git_log_since(cwd: Path, since_iso: str) -> List[str]:
    """Return commit messages since *since_iso* (ISO-8601 string).

    Returns an empty list when git is missing, not in a repo, or no commits
    match the window.
    """
    raw = _run(
        ["git", "log", f"--since={since_iso}", "--format=%s"],
        cwd=cwd,
    )
    return [line for line in raw.splitlines() if line.strip()]


def git_diff_stat(cwd: Path, since_ref: str) -> str:
    """Return short diffstat between *since_ref* and HEAD.

    Falls back to an empty string when *since_ref* doesn't exist or git fails.
    """
    return _run(
        ["git", "diff", "--stat", since_ref, "HEAD"],
        cwd=cwd,
    ).strip()


def git_current_branch(cwd: Path) -> str:
    """Return the abbreviated HEAD branch name, or '' when detached/missing."""
    return _run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=cwd,
    ).strip()


def git_status_short(cwd: Path) -> str:
    """Return porcelain v1 status output, or '' when not a repo."""
    return _run(
        ["git", "status", "--porcelain"],
        cwd=cwd,
    ).strip()


def git_modified_files(cwd: Path) -> List[str]:
    """Return list of modified/untracked file paths from git status.

    Porcelain v1 format: ``XY filename`` where XY are exactly 2 status chars
    followed by a space. We read raw (un-stripped) lines so the column offsets
    are preserved.
    """
    raw = _run(
        ["git", "status", "--porcelain"],
        cwd=cwd,
    )
    files: List[str] = []
    for line in raw.splitlines():
        # Each porcelain line is at least "XY f" (4 chars)
        if len(line) >= 4:
            files.append(line[3:].strip())
    return files
