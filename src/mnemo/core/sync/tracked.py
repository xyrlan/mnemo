"""What travels between machines, as one predicate (#642).

:func:`is_tracked` is the whole definition. Staging walks the vault with it,
and a pull applies it again to what the hub offers, so neither a ``.gitignore``
the user's machine carries nor a commit someone pushed by hand can widen the
set: a path that fails the predicate is never committed and never written.

Tracked: live ``shared/`` and ``shared/_inbox``, ``bots/*/briefings``,
``bots/*/memory``, ``.mnemo/extraction-state.json``, ``HOME.md`` and sync's own
three files. Everything else stays on the machine — ``shared/_archive``, the
rest of ``.mnemo/``, logs, ``.obsidian``, ``mnemo.config.json``, backups — and
so does every agent directory a test run or a probe left behind.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator

SYNC_DIR = ".mnemo/sync"
PRIMARY_FILE = SYNC_DIR + "/primary.json"
FORMAT_FILE = SYNC_DIR + "/format.json"
SEED_FILE = SYNC_DIR + "/config-seed.json"
_SYNC_FILES = frozenset({PRIMARY_FILE, FORMAT_FILE, SEED_FILE})

_ROOT_FILES = frozenset({"HOME.md", ".mnemo/extraction-state.json"})
_AGENT_DIRS = frozenset({"briefings", "memory"})

#: Where a machine keeps the version of a file that lost a conflict. Under
#: ``shared/_archive``, so it is never tracked and never read by recall.
LOSERS_DIR = "shared/_archive/sync-losers"

# An agent is named after the directory its session ran in, so a session in a
# temp directory leaves an agent named after one: a ``bg-``, ``claude-jobs-``
# or ``claude-tmp-`` prefix, a numeric uid followed by ``--``, or
# ``pytest-of-`` / ``scratchpad`` anywhere.
_LITTER = re.compile(r"^(?:bg-|claude-jobs-|claude-tmp-|[0-9]+--)|pytest-of-|scratchpad")

# Directories that are never content, at any depth.
_NEVER_DIRS = frozenset({".git", ".obsidian"})
# A backup (``x.bak``, ``x.bak.<stamp>``) or a writer's temp file.
_NEVER_NAME = re.compile(r"\.bak(?:\.|$)|\.tmp$|^\.DS_Store$|^Thumbs\.db$|^\.errors\.log")


def is_litter_agent(name: str) -> bool:
    """Whether ``bots/<name>`` was left by a test run or a probe."""
    return bool(_LITTER.search(name))


def is_tracked(rel: str) -> bool:
    """Whether the vault-relative POSIX path ``rel`` travels."""
    parts = rel.split("/")
    if not rel or any(p in ("", ".", "..") for p in parts) or "\\" in rel or ":" in parts[0]:
        return False
    if _NEVER_DIRS.intersection(parts[:-1]) or _NEVER_NAME.search(parts[-1]):
        return False
    if rel in _ROOT_FILES or rel in _SYNC_FILES:
        return True
    top = parts[0]
    if top == "shared":
        return len(parts) >= 2 and parts[1] != "_archive"
    if top == "bots":
        return len(parts) >= 4 and parts[2] in _AGENT_DIRS and not is_litter_agent(parts[1])
    return False


def _walk(root: Path, prefix: str) -> Iterator[str]:
    for dirpath, dirnames, filenames in os.walk(str(root)):
        here = Path(dirpath)
        # Symlinks are not content: Windows cannot check one out without a
        # privilege, and one pointing out of the vault would publish whatever
        # it points at.
        dirnames[:] = sorted(
            d for d in dirnames if d not in _NEVER_DIRS and not (here / d).is_symlink()
        )
        sub = here.relative_to(root).as_posix()
        base = prefix if sub == "." else f"{prefix}/{sub}"
        for name in sorted(filenames):
            path = here / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = f"{base}/{name}"
            if is_tracked(rel):
                yield rel


def walk(vault: Path) -> Iterator[str]:
    """Every tracked file on disk, as a vault-relative POSIX path."""
    vault = Path(vault)
    for rel in sorted(_ROOT_FILES | _SYNC_FILES):
        path = vault.joinpath(*rel.split("/"))
        if path.is_file() and not path.is_symlink():
            yield rel
    shared = vault / "shared"
    if shared.is_dir():
        for child in sorted(shared.iterdir()):
            if child.name == "_archive" or child.is_symlink():
                continue
            if child.is_dir():
                yield from _walk(child, f"shared/{child.name}")
            elif child.is_file() and is_tracked(f"shared/{child.name}"):
                yield f"shared/{child.name}"
    bots = vault / "bots"
    if bots.is_dir():
        for agent in sorted(bots.iterdir()):
            if agent.is_symlink() or not agent.is_dir() or is_litter_agent(agent.name):
                continue
            for kind in sorted(_AGENT_DIRS):
                sub = agent / kind
                if sub.is_dir() and not sub.is_symlink():
                    yield from _walk(sub, f"bots/{agent.name}/{kind}")
