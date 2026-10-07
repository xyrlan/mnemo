"""What a measure tool's report was measured with (#595).

A number re-read weeks later has to be tied back to the code and the data
that produced it. Every ``tools/measure_*.py`` report carries one
``provenance`` object (or, for a text report, one ``provenance: {...}``
line at the end):

- ``tool``, ``argv``, ``at`` (UTC), ``python``;
- ``commit`` and ``dirty`` of the checkout the tool ran from (``null`` outside
  a git checkout), and ``mnemo``: the directory ``import mnemo`` resolved to,
  since a bare run in a worktree imports the main checkout's code instead;
- ``vault``: a cheap fingerprint of the vault it read (``.md`` count, newest
  mtime and a digest of every page's path, size and mtime; no file is read);
- ``blind_spots``: what the tool could not see, filled in by the tool.

Home directories print as ``~`` and names in the vault's
``.mnemo/private-names.tsv`` print as their alias, since reports get pasted
into public PRs. Adding the key never changes another field of a report:
``stamp`` returns a copy and refuses a report that already has one.

Usage, in a tool's ``main``::

    prov = _provenance.provenance(__file__, argv, vault=vault)
    print(json.dumps(_provenance.stamp(data, prov), indent=1))  # --json
    print(_provenance.line(prov))                               # text
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

KEY = "provenance"
_TOOLS_ROOT = Path(__file__).resolve().parents[1]

PathLike = Union[str, "os.PathLike[str]"]


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(["git", "-C", str(root)] + list(args),
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def git_state(root: PathLike) -> Tuple[Optional[str], Optional[bool]]:
    """``(commit, dirty)`` of the checkout holding ``root``; ``(None, None)`` outside one.

    Dirty means a tracked file differs from the commit; untracked files (a
    scratch report, a cache) do not count.
    """
    root = Path(root)
    head = _git(root, "rev-parse", "HEAD")
    if not head or not head.strip():
        return None, None
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    return head.strip(), (None if status is None else bool(status.strip()))


def load_aliases(vault: Optional[PathLike]) -> List[Tuple[str, str]]:
    """``name<TAB>alias`` rows of ``<vault>/.mnemo/private-names.tsv``, longest name first."""
    if vault is None:
        return []
    try:
        text = (Path(vault) / ".mnemo" / "private-names.tsv").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    out = []
    for row in text.splitlines():
        name, _, alias = row.partition("\t")
        if name.strip():
            out.append((name.strip(), alias.strip() or "a-private-repo"))
    return sorted(out, key=lambda na: -len(na[0]))


def redact(text: str, aliases: Sequence[Tuple[str, str]] = ()) -> str:
    """The home directory to ``~`` and each private name to its alias."""
    home = os.path.expanduser("~")
    if home and home not in ("~", os.sep):
        text = text.replace(home, "~")
    for name, alias in aliases:
        text = re.sub(r"(?i)(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(name), alias, text)
    return text


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def vault_fingerprint(vault: Optional[PathLike]) -> Optional[Dict[str, Any]]:
    """Page count, newest mtime and a digest of (path, size, mtime) over the vault's ``.md`` files.

    Dot directories (``.mnemo``, ``.obsidian``, ``.git``) are skipped: they
    hold caches and reports, not pages. Only ``stat`` is called, so 10k pages
    cost a fraction of a second. ``None`` without a vault; a missing
    directory reads as ``exists: false``.
    """
    if vault is None:
        return None
    root = Path(vault).expanduser()
    if not root.is_dir():
        return {"exists": False, "pages": 0, "newest": None, "digest": None}
    digest = hashlib.sha256()
    pages = 0
    newest = None  # type: Optional[float]
    for dirpath, dirs, files in os.walk(str(root)):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            pages += 1
            newest = st.st_mtime if newest is None else max(newest, st.st_mtime)
            rel = os.path.relpath(path, str(root)).replace(os.sep, "/")
            digest.update(("%s\0%d\0%d\n" % (rel, st.st_size, st.st_mtime_ns)).encode("utf-8"))
    return {"exists": True, "pages": pages,
            "newest": _iso(newest) if newest is not None else None,
            "digest": "sha256:" + digest.hexdigest()[:16]}


def transcripts_blind_spot(projects: Optional[PathLike]) -> Optional[str]:
    """Where Claude Code's transcript history on disk begins, as a blind spot.

    Claude Code deletes transcripts older than ``cleanupPeriodDays`` (30 by
    default, #596), so any count over ``~/.claude/projects`` cannot see the
    sessions before the oldest file still there.
    """
    if projects is None:
        return None
    root = Path(projects).expanduser()
    oldest = None  # type: Optional[float]
    try:
        for path in root.glob("*/*.jsonl"):
            try:
                m = path.stat().st_mtime
            except OSError:
                continue
            oldest = m if oldest is None else min(oldest, m)
    except OSError:
        return None
    if oldest is None:
        return "no transcripts under %s" % root
    return ("transcripts under %s go back only to %s (oldest file's last write); "
            "Claude Code deletes older ones (cleanupPeriodDays)" % (root, _iso(oldest)[:10]))


def _mnemo_dir() -> Optional[str]:
    mod = sys.modules.get("mnemo")
    path = getattr(mod, "__file__", None) if mod is not None else None
    return os.path.dirname(os.path.abspath(path)) if path else None


def provenance(tool: PathLike, argv: Optional[Sequence[str]] = None, *,
               vault: Optional[PathLike] = None,
               blind_spots: Iterable[Optional[str]] = (),
               root: Optional[PathLike] = None,
               now: Optional[datetime] = None) -> Dict[str, Any]:
    """The provenance object for one run of ``tool`` (its ``__file__`` or name).

    ``argv`` is what ``main`` was given (``None`` reads ``sys.argv[1:]``, as
    argparse does). ``root`` is the checkout whose commit is recorded; it
    defaults to the one holding this file. ``None`` entries in
    ``blind_spots`` are dropped, so a tool can pass a helper's result as is.
    """
    aliases = load_aliases(Path(vault).expanduser() if vault is not None else None)
    commit, dirty = git_state(root if root is not None else _TOOLS_ROOT)
    mnemo_dir = _mnemo_dir()
    args = list(sys.argv[1:] if argv is None else argv)
    return {
        "tool": Path(str(tool)).stem,
        "argv": [redact(str(a), aliases) for a in args],
        "at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "python": platform.python_version(),
        "commit": commit,
        "dirty": dirty,
        "mnemo": redact(mnemo_dir, aliases) if mnemo_dir else None,
        "vault": (dict(vault_fingerprint(vault) or {}, path=redact(str(Path(vault).expanduser()), aliases))
                  if vault is not None else None),
        "blind_spots": [redact(s, aliases) for s in blind_spots if s],
    }


def stamp(report: Dict[str, Any], prov: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of ``report`` with ``prov`` under ``provenance``; no other field changes."""
    if KEY in report:
        raise ValueError("report already has a %r field" % KEY)
    out = dict(report)
    out[KEY] = prov
    return out


def line(prov: Dict[str, Any]) -> str:
    """``prov`` as the one line a text report ends with."""
    return "%s: %s" % (KEY, json.dumps(prov, sort_keys=True, ensure_ascii=False))
