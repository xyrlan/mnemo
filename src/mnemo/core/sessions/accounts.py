"""Which Claude config dirs hold background sessions (#640).

Claude Code keeps a session's job record and its daemon roster under its
config dir: ``~/.claude`` by default, ``$CLAUDE_CONFIG_DIR`` when that is set.
One machine can have several — the desktop app's accounts create
``~/.claude-<slug>`` each, with ``projects``, the settings files and the rest
of the user's content linked back to ``~/.claude`` and ``jobs``, ``sessions``
and ``daemon`` their own. Reading ``~/.claude/jobs`` alone, which is what the
roster did until #640, loses every background session started on any other
account: on 2026-10-13 two dispatch children were ``working`` on disk and in
neither ``mnemo sessions`` nor the watcher that was to wake them.

Three sources, read in this order, each for its own reason:

- ``$CLAUDE_CONFIG_DIR`` — the account this process runs on. Certain, and
  where a child this process spawns is registered.
- ``~/.claude`` — the default account, always: a session on an account dir
  still has children and siblings there.
- Every config dir an earlier mnemo process ran under, remembered in
  ``<vault>/.mnemo/claude-config-dirs.jsonl``. The environment names one
  account only; the watchers are one per vault and outlive the session that
  started them, and a parent on one account can have children on another.
  A child's own hooks run in the child's environment, so its account is on
  file by the time anything has to find it.

Not globbed: ``~/.claude-*`` is one app's naming convention, and
``CLAUDE_CONFIG_DIR`` may point anywhere. What is read is what was seen.

A dir that is missing, unreadable or from another machine costs nothing: the
readers skip it. Everything here fails open to the default account.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Union

#: Claude Code's own variable for a non-default config dir.
CONFIG_ENV = "CLAUDE_CONFIG_DIR"

#: One ``{"config_dir": ...}`` row per account dir mnemo has run under.
#: Append-only, one ``O_APPEND`` line per new dir: two accounts starting at
#: once cannot lose each other's row, and there is nothing to compact — the
#: file grows by one line per account, ever.
LOG_NAME = "claude-config-dirs.jsonl"


@dataclass(frozen=True)
class Root:
    """One Claude config dir: where its daemon state and its jobs live."""

    home: Path
    jobs: Path


def log_path(vault_root: Union[Path, str]) -> Path:
    return Path(vault_root) / ".mnemo" / LOG_NAME


def _key(path: Union[Path, str]) -> str:
    """One spelling per directory, so a symlink or a case is not a second root."""
    try:
        return os.path.normcase(os.path.realpath(str(path)))
    except (OSError, ValueError):  # pragma: no cover - realpath is total on posix
        return os.path.normcase(str(path))


def current(env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The config dir this process was told to use, or ``None`` for the default."""
    value = (os.environ if env is None else env).get(CONFIG_ENV, "")
    value = value.strip() if isinstance(value, str) else ""
    if not value:
        return None
    return Path(os.path.abspath(os.path.expanduser(value)))


def remembered(vault_root: Union[Path, str]) -> List[Path]:
    """Every config dir on file, oldest first. ``[]`` when there is no file."""
    try:
        text = log_path(vault_root).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    out: List[Path] = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        value = row.get("config_dir") if isinstance(row, dict) else None
        if isinstance(value, str) and value:
            out.append(Path(value))
    return out


def remember(vault_root: Union[Path, str], config_dir: Union[Path, str]) -> bool:
    """Put *config_dir* on file unless it already is. True when a row was written.

    Never creates the vault: a machine where mnemo was never initialised has
    nowhere to remember anything, and gets the environment and the default.
    Never raises.
    """
    try:
        if not Path(vault_root).is_dir():
            return False
        wanted = _key(config_dir)
        if any(_key(known) == wanted for known in remembered(vault_root)):
            return False
        path = log_path(vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"config_dir": str(Path(config_dir))}, ensure_ascii=False) + "\n"
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as fh:
            fh.write(line.encode("utf-8"))
        return True
    except (OSError, TypeError, ValueError):
        return False


def _default_vault() -> Optional[Path]:
    try:
        from mnemo.core import config, paths

        return paths.vault_root(config.load_config())
    except Exception:  # noqa: BLE001 — a broken config must not empty the roster
        return None


def roots(*, env: Optional[Mapping[str, str]] = None,
          vault_root: Union[Path, str, None] = None) -> List[Root]:
    """Every config dir to read, the current account first, each one once.

    Records the current account as a side effect, so the dir is on file from
    the first mnemo process that runs under it — a hook, the statusline, a
    dispatch — with no call site to forget. The common case is one read of a
    file a few lines long and no write.
    """
    from mnemo.core.sessions import jobs, liveness

    out: List[Root] = []
    seen = set()

    def add(home: Path, jobs_path: Path) -> None:
        key = _key(jobs_path)
        if key not in seen:
            seen.add(key)
            out.append(Root(home=home, jobs=jobs_path))

    default_home = liveness.claude_home()
    here = current(env)
    if here is not None and _key(here) != _key(default_home):
        add(here, here / "jobs")
    else:
        here = None
    add(default_home, jobs.jobs_dir())

    vault = _default_vault() if vault_root is None else Path(vault_root)
    if vault is not None:
        if here is not None:
            remember(vault, here)
        for known in remembered(vault):
            if _key(known) != _key(default_home):
                add(known, known / "jobs")
    return out


def state_paths(short_id: str) -> List[Path]:
    """Where *short_id*'s ``state.json`` would be, one path per config dir.

    The current account's comes first: it is where ``claude --bg`` run from
    this process registers the child.
    """
    return [root.jobs / short_id / "state.json" for root in roots()]
