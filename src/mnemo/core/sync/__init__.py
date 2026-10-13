"""One vault on several of your own machines, through a git remote (#642).

Off until ``mnemo sync init`` or ``mnemo sync join`` is typed, and called by
no hook. :mod:`~mnemo.core.sync.tracked` says what travels,
:mod:`~mnemo.core.sync.engine` is the five commands,
:mod:`~mnemo.core.sync.git` is how the machine's ``git`` is run, and
:mod:`~mnemo.core.sync.state` is what one machine remembers.
"""
from __future__ import annotations

from mnemo.core.sync.engine import Outcome, init, join, pull, push, status
from mnemo.core.sync.git import GitError, GitMissing
from mnemo.core.sync.state import FORMAT_VERSION, lock_path, machine_id

__all__ = [
    "FORMAT_VERSION", "GitError", "GitMissing", "Outcome",
    "init", "join", "lock_path", "machine_id", "pull", "push", "status",
]
