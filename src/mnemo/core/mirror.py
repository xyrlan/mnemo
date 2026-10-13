# src/mnemo/core/mirror.py
"""Claude → vault mirror: Claude Code's per-project memory into ``bots/<agent>/memory/``.

Every copy passes through :func:`mnemo.core.redact.redact_secrets` on the way
in (#649): the vault is what the extractor reads and what ``mnemo sync``
publishes, and before this the mirror was the one writer that bypassed the
redactor — and ``mnemo redact --apply`` leaves ``bots/*/memory`` alone, so a
credential shape in a note stopped every push. Claude Code's own files are
only read. A file with nothing to redact is copied byte for byte.

This used to shell out to ``rsync -a`` when it was on ``PATH``. rsync cannot
redact, and would have copied a redacted file back over every run because its
size differs from its source's; the Python walk keeps rsync's size+mtime
quick check and is one process instead of one per project.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, NamedTuple, Optional, Tuple

from mnemo.core import agent, locks, paths, redact


class Report(NamedTuple):
    """What one run did: files seen, files (re)written, files whose copy
    differs from its source by redaction."""

    seen: int = 0
    written: int = 0
    redacted: int = 0


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
        # Canonical (#225): this name selects the vault memory dir to mirror
        # into. Naive resolution mirrored a worktree session's memory into an
        # orphan `bots/<repo>-wt-*/` namespace nothing reads.
        return agent.resolve_canonical_agent(str(decoded)).name
    # Fallback for paths that no longer exist on disk: use the historical heuristic.
    tail = "-".join(parts[3:])
    if tail:
        return tail
    return parts[-1] or "root"


def _redactor_fingerprint() -> str:
    """A digest of the redactor's source: it changes when a rule does.

    The quick check below trusts a copy that has its source's size and
    mtime, because this mirror never writes one that carries a secret. That
    holds for the rules the copy was checked against, so the digest is kept
    with the vault and a run under a new one reads every source again.
    """
    try:
        return hashlib.sha256(Path(redact.__file__).read_bytes()).hexdigest()[:16]
    except (OSError, TypeError, AttributeError):
        return ""


def _state_path(vault: Path) -> Path:
    return vault / ".mnemo" / "mirror-state.json"


def _all_checked(vault: Path) -> bool:
    """Whether every copy was checked against the current redactor."""
    try:
        state = json.loads(_state_path(vault).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    fp = _redactor_fingerprint()
    return bool(fp) and state.get("redactor") == fp


def _mark_checked(vault: Path) -> None:
    path = _state_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"redactor": _redactor_fingerprint()}), encoding="utf-8")
    os.replace(tmp, path)


def _redacted(raw: bytes) -> bytes:
    """The bytes the vault gets for *raw*: *raw* itself when it holds nothing
    the redactor replaces, so a clean copy is byte for byte its source.

    ``surrogateescape`` carries a byte that is not UTF-8 through the pass
    unchanged, rather than turning it into U+FFFD on the way.
    """
    text, n = redact.redact_secrets(raw.decode("utf-8", errors="surrogateescape"))
    return text.encode("utf-8", errors="surrogateescape") if n else raw


def _write(dst: Path, data: bytes) -> None:
    """Atomic: a sibling ``.tmp`` then ``os.replace``, so the extractor never
    reads half a page. ``.tmp`` files are neither scanned nor synced."""
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dst)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _copy_file(src: Path, dst: Path, *, check_all: bool) -> Tuple[bool, bool]:
    """Bring *dst* in line with the redacted *src*. Returns (written, redacted).

    The quick check is rsync's: a copy with its source's size and mtime is
    up to date, and is not read. It is skipped on a *check_all* run, and it
    never fires for a redacted copy, whose size differs — those are read and
    compared, and left alone when they already say what the source would.
    """
    st = src.stat()
    try:
        dt: Optional[os.stat_result] = dst.stat()
    except FileNotFoundError:
        dt = None
    if (dt is not None and not check_all
            and dt.st_mtime_ns == st.st_mtime_ns and dt.st_size == st.st_size):
        return False, False
    raw = src.read_bytes()
    data = _redacted(raw)
    redacted = data is not raw
    if dt is not None and dt.st_size == len(data) and dst.read_bytes() == data:
        if dt.st_mtime_ns != st.st_mtime_ns:
            shutil.copystat(src, dst)
        return False, redacted
    _write(dst, data)
    shutil.copystat(src, dst)
    return True, redacted


def _copy_tree(src: Path, dst: Path, *, check_all: bool) -> "Report":
    """Pure-Python copy of *src* into *dst*, redacting on the way (#649).
    Never deletes from *dst*: a note the user wrote there stays."""
    seen = written = redacted = 0
    dst.mkdir(parents=True, exist_ok=True)
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(files):
            w, r = _copy_file(Path(root) / f, target_dir / f, check_all=check_all)
            seen += 1
            written += int(w)
            redacted += int(r)
    return Report(seen, written, redacted)


def _has_files(src: Path) -> bool:
    """True when *src* holds at least one file, at any depth.

    Claude Code creates ``~/.claude/projects/<cwd>/memory/`` for every cwd a
    session runs in, pytest tmp dirs and job probes included, and leaves it
    empty unless something is saved. Mirroring an empty one made an agent
    directory out of nothing: 48 of them on the maintainer's machine (#420).
    """
    for _root, _dirs, files in os.walk(src):
        if files:
            return True
    return False


def mirror_all(cfg: dict[str, Any], projects_root: Optional[Path] = None) -> Report:
    """Mirror every project's memory under *projects_root* (default
    ``~/.claude/projects``) into the vault. Returns what the run did; an
    empty :class:`Report` when there was nothing to do or another run holds
    the lock.

    Not on a sync satellite (#643): ``bots/*/memory`` is the primary's to
    write, and two machines mirroring by file name would overwrite each
    other's ``MEMORY.md`` every session.
    """
    from mnemo.core.sync import roles

    vault = paths.vault_root(cfg)
    if roles.is_satellite(vault, cfg):
        return Report()
    paths.bots_dir(cfg).mkdir(parents=True, exist_ok=True)
    if projects_root is None:
        projects_root = _claude_projects_root()
    if not projects_root.exists():
        return Report()
    lock_dir = vault / ".mirror.lock"
    seen = written = redacted = 0
    with locks.try_lock(lock_dir) as held:
        if not held:
            return Report()
        # Copies made before #649, or checked against an older redactor, are
        # byte-identical to their source and pass the quick check: once per
        # redactor version, every source is read.
        check_all = not _all_checked(vault)
        for project_dir in sorted(projects_root.iterdir()):
            memory_src = project_dir / "memory"
            if not memory_src.is_dir() or not _has_files(memory_src):
                continue
            agent_name = _agent_from_project_dir(project_dir.name)
            target = paths.memory_dir(cfg, agent_name)
            done = _copy_tree(memory_src, target, check_all=check_all)
            seen, written, redacted = seen + done.seen, written + done.written, redacted + done.redacted
        if check_all:
            _mark_checked(vault)
    return Report(seen, written, redacted)
