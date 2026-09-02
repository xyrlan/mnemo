"""Thin wrapper around ``git`` and the ``gh`` CLI for self-fix PR operations.

All functions return ``None`` / ``False`` when the tool is unavailable or
the underlying command fails — callers must handle the None case.

Self-fix builds every PR inside a throwaway ``git worktree``. It must never
run ``git checkout`` in the live repo: the autopilot shares that checkout
with whoever is working in it, and moving ``HEAD`` underneath them silently
lands their next commit on the autopilot's branch.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional

from mnemo.autopilot.core import network

WORKTREE_PREFIX = "mnemo-selffix-"


def _run(args: List[str], *, cwd: Path) -> Optional[subprocess.CompletedProcess]:
    """Run *args* in *cwd*, returning ``None`` if the binary is unavailable."""
    try:
        return subprocess.run(args, capture_output=True, text=True, cwd=str(cwd))
    except (FileNotFoundError, OSError, NotADirectoryError):
        return None


def resolve_repo_root(path: Path) -> Optional[Path]:
    """Return the git toplevel containing *path*, or ``None`` if there is none.

    Used to anchor self-fix on the repo that actually holds the files it
    edits, rather than on whatever repo the process happens to be run from.
    """
    if not path.is_dir():
        return None
    result = _run(["git", "rev-parse", "--show-toplevel"], cwd=path)
    if result is None or result.returncode != 0:
        return None
    toplevel = result.stdout.strip()
    return Path(toplevel) if toplevel else None


def create_worktree(branch_name: str, *, repo_root: Path) -> Optional[Path]:
    """Check *branch_name* out into a fresh worktree outside the repo.

    The live checkout's ``HEAD``, index and working tree are left untouched.
    Returns the worktree path on success, ``None`` on failure (including when
    the branch already exists).
    """
    try:
        target = Path(tempfile.mkdtemp(prefix=WORKTREE_PREFIX))
    except OSError:
        return None
    # git worktree add refuses to write into an existing directory.
    try:
        target.rmdir()
    except OSError:
        return None
    result = _run(
        ["git", "worktree", "add", "-b", branch_name, str(target), "HEAD"],
        cwd=repo_root,
    )
    if result is None or result.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        return None
    return target


def mirror_paths(
    paths: Iterable[Path], *, source_root: Path, worktree: Path
) -> None:
    """Replay the live tree's state for *paths* inside *worktree*.

    A path that still exists is copied over; a path that has gone (an archived
    rule's old location) is deleted from the worktree. Paths outside
    *source_root* are skipped — the perimeter guard already rejects them, and
    they have no meaningful location in the worktree.
    """
    root = source_root.resolve()
    for path in paths:
        try:
            rel = path.resolve().relative_to(root)
        except ValueError:
            continue
        target = worktree / rel
        if path.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        elif target.exists():
            target.unlink()


def commit_all(message: str, *, worktree: Path) -> bool:
    """Stage everything in *worktree* and commit it.

    Returns ``False`` when there is nothing to commit or git fails — either
    way there is no commit to push, so the caller must not open a PR.
    """
    staged = _run(["git", "add", "-A"], cwd=worktree)
    if staged is None or staged.returncode != 0:
        return False
    diff = _run(["git", "diff", "--cached", "--quiet"], cwd=worktree)
    if diff is None or diff.returncode == 0:
        return False  # nothing staged
    result = _run(["git", "commit", "-m", message], cwd=worktree)
    return result is not None and result.returncode == 0


def remove_worktree(worktree: Path, *, repo_root: Path) -> None:
    """Tear down *worktree*, forcing removal even if it is dirty."""
    if not worktree.exists():
        return
    _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo_root)
    shutil.rmtree(worktree, ignore_errors=True)


def push_branch(branch_name: str, *, repo_root: Path) -> bool:
    """Push *branch_name* to ``origin``.

    Pass the worktree as *repo_root* — pushing from the live checkout is fine
    for git, but keeping every self-fix command inside the worktree keeps the
    blast radius of a bad argument there too.

    Returns ``True`` on success, ``False`` on failure — including when
    ``autopilot.network.enabled`` is off, the backstop for any caller that
    reaches here without its own gate.
    """
    if not network.enabled():
        return False
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

    Returns ``None`` when ``autopilot.network.enabled`` is off, ``gh`` is
    unavailable, the command fails, or the output cannot be parsed as an
    integer.
    """
    if not network.enabled():
        return None
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


def open_issue(
    *,
    title: str,
    body: str,
    labels: List[str],
    repo_root: Path,
) -> Optional[int]:
    """Open a GitHub issue and return its number.

    Used for findings that carry no diff — a report with nothing to merge is
    an issue, not a pull request.  ``gh issue create`` prints the issue URL,
    whose last segment is the number.

    Returns ``None`` when ``autopilot.network.enabled`` is off.
    """
    if not network.enabled():
        return None
    cmd = ["gh", "issue", "create", "--title", title, "--body", body]
    for label in labels:
        cmd += ["--label", label]
    result = _run(cmd, cwd=repo_root)
    if result is None or result.returncode != 0:
        return None
    url = (result.stdout or "").strip().rsplit("/", 1)
    try:
        return int(url[-1])
    except (ValueError, IndexError):
        return None
