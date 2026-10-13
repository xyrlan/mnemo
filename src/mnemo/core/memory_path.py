"""The real path of an auto-memory file an account dir reaches through a link (#637).

A Claude Code session started with ``CLAUDE_CONFIG_DIR=~/.claude-<slug>``,
where that directory's ``projects`` entry is a symlink to
``~/.claude/projects`` (the layout the desktop app's accounts create), is told
its auto-memory directory is ``~/.claude-<slug>/projects/<project>/memory/``.
Claude Code prompts for every write spelled that way: the requested path is
not a protected one, the symlink check resolves it into ``~/.claude``, and its
exemption for memory markdown is matched on the real spelling. On the
maintainer's transcripts, six days to 2026-10-13, 110 such writes waited for a
person, up to 6 hours, and none of the 71 spelled through ``~/.claude`` did
(``tools/measure_memory_write_prompts.py --days 6``).

:func:`real_memory_path` names the same file by the spelling Claude Code
handles without a prompt, and answers ``None`` for everything else. It
approves nothing. The PreToolUse hook uses it two ways, because Claude Code
lets a hook move a write that creates a file and not one that changes it (the
hook's ``_memory_write`` says why): a new file's path goes back as
``updatedInput`` with no permission decision, and an edit is denied with
:func:`redirect_reason`, which sends the model to the real path. Either way
Claude Code decides on that path as it would have had the model asked for it
first. A hook that answers ``allow`` for the account spelling does not lift
the prompt (tried on 2.1.296), so there is no shorter way.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

#: The environment variable that moves Claude Code's config dir off ``~/.claude``.
CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"


def real_memory_path(file_path: object, config_dir: Optional[str]) -> Optional[str]:
    """The real path of *file_path* when it is a markdown file inside
    ``<config_dir>/projects/<project>/memory/`` and ``projects`` is reached
    through a symlink; ``None`` for any other path.

    Only a link at or above ``projects`` is resolved. A path whose real
    location is anything but ``<real projects>/<project>/memory/…`` (a memory
    file that is itself a link, a ``..`` out of the directory) is left alone,
    and so is one whose two spellings are equal, which is every write of a
    session under the default dir. A ``projects`` link that leads nowhere
    answers ``None``. An ``OSError`` is the caller's to swallow.
    """
    if not isinstance(file_path, str) or not file_path or not config_dir:
        return None
    asked = Path(file_path)
    projects = Path(os.path.expanduser(config_dir)) / "projects"
    try:
        parts = asked.relative_to(projects).parts
    except ValueError:
        return None
    # <project>/memory/<…>.md, spelled without a way back out.
    if len(parts) < 3 or parts[1] != "memory" or ".." in parts:
        return None
    if asked.suffix.lower() != ".md":
        return None
    real_projects = os.path.realpath(str(projects))
    if not os.path.isdir(real_projects):
        return None
    real = Path(real_projects).joinpath(*parts)
    if real == asked or Path(os.path.realpath(file_path)) != real:
        return None
    return str(real)


def redirect_reason(asked: str, real: str) -> str:
    """What the model is told when an edit of *asked* is denied: the same
    file's real path, and the two calls that change it there."""
    return (
        "%s is a symlinked spelling of an auto-memory file. Its real path is:\n"
        "%s\n"
        "Nothing was changed. Read the file at its real path, then make this "
        "same change there; address the other files of this memory directory "
        "by their real path too." % (asked, real)
    )
