"""How far this machine is from the hub, for ``doctor`` and the statusline (#644).

One summary that both read, so the number on the statusline is the number
``mnemo doctor`` prints. Everything in it comes from this machine: the state
file, the two branch tips as last fetched, and a walk of the tracked set.
Nothing here talks to the hub except :func:`reach`, which ``doctor`` alone
calls, under a bound.

The git part — commits ahead and behind, and how many tracked files differ
from the branch — is kept under ``.mnemo/`` for :data:`CACHE_SECONDS`.
Counting the pending files walks the tracked set (half a second warm on a
vault of 7,000 tracked files, measured 2026-10-13), and the statusline is
rendered on every refresh. Every pull and push drops the cache, so a vault
that was just pushed reads as in step on the next render.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.sync import state as st
from mnemo.core.sync import tracked

#: A pull or push older than this is reported as stale, by ``doctor`` and the
#: statusline alike. A day: a machine used today has pulled today (#643), and
#: one asleep for longer is the case the warning exists for.
STALE_AFTER_SECONDS = 24 * 3600

#: How long ``doctor`` waits for the hub before saying it could not reach it.
REACH_TIMEOUT = 3.0

#: How long the counts of ahead, behind and pending stay good for the statusline.
CACHE_SECONDS = 120

CACHE_FILE = ".mnemo/sync-health.json"


def _parse(stamp: Optional[str]) -> Optional[datetime]:
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _now(now: Optional[datetime]) -> datetime:
    return now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)


def _ago(stamp: Optional[str], now: datetime) -> Optional[int]:
    then = _parse(stamp)
    if then is None:
        return None
    return max(0, int((now - then).total_seconds()))


def ago_text(seconds: Optional[int]) -> str:
    """``never``, ``3 d``, ``5 h``, ``12 min`` or ``40 s``."""
    if seconds is None:
        return "never"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if seconds >= size:
            return f"{seconds // size} {unit}"
    return f"{seconds} s"


def cache_path(vault: Path) -> Path:
    return Path(vault).joinpath(*CACHE_FILE.split("/"))


def drop_cache(vault: Path) -> None:
    """Forget the counts: the engine calls this after every pull and push."""
    try:
        cache_path(vault).unlink()
    except OSError:
        pass


def _count(vault: Path) -> Dict[str, int]:
    """Commits ahead and behind as last fetched, and tracked files that
    differ from the branch. Three git calls and one walk."""
    from mnemo.core.sync import engine

    git = engine.Git(vault)
    tips = engine._tips(git)
    ahead = behind = 0
    if tips.head and tips.theirs and tips.head != tips.theirs:
        counts = git.out("rev-list", "--left-right", "--count",
                         f"{tips.head}...{tips.theirs}").split()
        if len(counts) == 2:
            ahead, behind = int(counts[0]), int(counts[1])
    return {"ahead": ahead, "behind": behind, "pending": engine._pending(git, tips.head_tree)}


def counts(vault: Path, *, max_age: float = CACHE_SECONDS,
           now: Optional[datetime] = None) -> Dict[str, Any]:
    """The cached counts when they are younger than ``max_age`` seconds,
    else fresh ones, written back. ``countsAgeSeconds`` says which."""
    now = _now(now)
    path = cache_path(vault)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if max_age > 0 and isinstance(data, dict):
        age = _ago(data.get("at"), now)
        if age is not None and age <= max_age and all(
            isinstance(data.get(k), int) for k in ("ahead", "behind", "pending")
        ):
            return {"ahead": data["ahead"], "behind": data["behind"],
                    "pending": data["pending"], "countsAt": data["at"],
                    "countsAgeSeconds": age}
    fresh = _count(vault)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        atomic_write_bytes(path, (json.dumps(dict(fresh, at=stamp), sort_keys=True) + "\n")
                           .encode("utf-8"))
    except OSError:
        pass
    return dict(fresh, countsAt=stamp, countsAgeSeconds=0)


def summary(vault: Path, machine: str, *, max_age: float = CACHE_SECONDS,
            now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Where this machine stands, or ``None`` when sync is off here.

    Reads the state file and the cached counts; never the hub.
    """
    vault = Path(vault)
    if not st.enabled(vault):
        return None
    now = _now(now)
    state = st.load(vault)
    hub_format = state.get("hubFormatVersion")
    pull_ago = _ago(state.get("lastPull"), now)
    push_ago = _ago(state.get("lastPush"), now)
    last_error = state.get("lastError") if isinstance(state.get("lastError"), dict) else None
    out: Dict[str, Any] = {
        "enabled": True,
        "machineId": machine,
        "role": st.role_of(vault, machine),
        "primary": st.primary_of(vault),
        "lastPull": state.get("lastPull"),
        "lastPullAgoSeconds": pull_ago,
        "pullStale": pull_ago is None or pull_ago > STALE_AFTER_SECONDS,
        "lastPush": state.get("lastPush"),
        "lastPushAgoSeconds": push_ago,
        "pushesRefused": int(state.get("pushesRefused") or 0),
        "refused": [r for r in state.get("refused") or [] if isinstance(r, dict)],
        "conflictsResolved": int(state.get("conflictsResolved") or 0),
        "losersKept": int(state.get("losersKept") or 0),
        "losersDir": tracked.LOSERS_DIR,
        "formatVersion": st.FORMAT_VERSION,
        "hubFormatVersion": hub_format,
        "formatSupported": hub_format is None or hub_format == st.FORMAT_VERSION,
        "lastError": last_error,
        "staleAfterSeconds": STALE_AFTER_SECONDS,
        "stateProblem": st.problem(vault),
    }
    from mnemo.core.sync import engine

    out["gitLocks"] = engine.stale_git_locks(vault, now=now.timestamp())
    out.update(counts(vault, max_age=max_age, now=now))
    # A push is late only when there is something to push: a satellite that
    # joined and has written nothing yet has never needed one (#655).
    waiting = bool(out.get("pending") or out.get("ahead"))
    out["pushStale"] = waiting and (push_ago is None or push_ago > STALE_AFTER_SECONDS)
    return out


def reach(vault: Path, *, timeout: float = REACH_TIMEOUT) -> Dict[str, Any]:
    """Whether the hub answers within ``timeout`` seconds: one ``ls-remote``.

    ``ok`` is whether it answered; ``message`` is what ``doctor`` prints.
    A hub that answers but holds no branch yet is reachable.
    """
    from mnemo.core.sync import engine
    from mnemo.core.sync.git import GitError, GitMissing, GitTimeout

    git = engine.Git(vault)
    timer = st.Timer()
    try:
        proc = git.run("ls-remote", "--exit-code", "-q", "origin", engine.LOCAL_REF,
                       check=False, timeout=timeout)
    except GitTimeout:
        return {"ok": False, "ms": timer.ms(),
                "message": f"not reachable within {timeout:g} s"}
    except GitMissing:
        return {"ok": False, "ms": timer.ms(), "message": "git is not on PATH"}
    except GitError as exc:
        return {"ok": False, "ms": timer.ms(), "message": exc.stderr}
    ms = timer.ms()
    if proc.returncode == 0:
        return {"ok": True, "ms": ms, "message": f"reachable ({ms} ms)"}
    if proc.returncode == 2:
        return {"ok": True, "ms": ms, "message": f"reachable, holds no vault yet ({ms} ms)"}
    from mnemo.core.sync import git as gitmod

    said = gitmod._tail(proc.stderr) or f"exit {proc.returncode}"
    return {"ok": False, "ms": ms, "message": f"not reachable ({said})"}


def segment(summary_: Optional[Dict[str, Any]]) -> str:
    """The statusline's one segment, or ``''`` when there is nothing to say.

    ``sync 3↑`` when tracked files are waiting to be pushed (``sync ↑`` when a
    commit is, and no file), ``sync 2 d behind`` when the last pull is older
    than :data:`STALE_AFTER_SECONDS` or there was none, both when both. In
    step, or with sync off, nothing.
    """
    if not summary_ or not summary_.get("enabled"):
        return ""
    parts = []
    pending = int(summary_.get("pending") or 0)
    ahead = int(summary_.get("ahead") or 0)
    if pending:
        parts.append(f"{pending}↑")
    elif ahead:
        parts.append("↑")
    if summary_.get("pullStale"):
        ago = summary_.get("lastPullAgoSeconds")
        parts.append("never pulled" if ago is None else f"{ago_text(ago)} behind")
    return "sync " + " ".join(parts) if parts else ""
