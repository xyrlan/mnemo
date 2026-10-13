"""init, join, pull, push and status (#642).

The vault is a git work tree, but sync never asks git to merge. Between runs
the local branch always sits on a commit the hub has, and everything this
machine wrote since is simply the work tree. A pull compares three trees —
that commit, the hub's tip, and the work tree — and for each tracked path
takes the hub's bytes, keeps the local ones, or (when both sides changed it)
gives the path to the primary's version. A push pulls, makes one commit of
what is left, and sends it. There is no moment at which a file holds conflict
markers or the index holds an unmerged entry, because nothing ever creates
either.

Nothing here is called by a hook. Every function takes the vault and the
machine id as arguments and reads no global config, which is what lets the
tests drive two machines in one process.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mnemo.core import locks
from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.sync import git as gitmod
from mnemo.core.sync import health
from mnemo.core.sync import state as st
from mnemo.core.sync import tracked
from mnemo.core.sync.git import Git, GitError, GitMissing, GitTimeout

BRANCH = "main"
LOCAL_REF = "refs/heads/" + BRANCH
REMOTE_REF = "refs/remotes/origin/" + BRANCH

#: How many times a push pulls and tries again when the hub moved first.
PUSH_ATTEMPTS = 4

# A sync that died holding the lock is reclaimed after this long. Longer than
# any real run: the first push of a whole vault is the slow one.
_LOCK_STALE_SECONDS = 900.0

_MACHINE_TRAILER = "Sync-Machine: "
_OVERRODE_TRAILER = "Sync-Overrode: "
_SYMLINK, _GITLINK = "120000", "160000"

# What git is told about the work tree, for the day the user runs git in the
# vault by hand. The tracked set itself is `tracked.is_tracked`, not this.
_ATTRIBUTES = "* -text -filter -ident\n"
# In `.git/`: says this repository is sync's own and not one the user made.
_MARKER = "mnemo-sync"
_EXCLUDE = """\
# Written by `mnemo sync`. A convenience for `git status`: what sync commits
# is decided by mnemo, not by this file.
/*
!/HOME.md
!/shared/
/shared/_archive/
!/bots/
/bots/*/*
!/bots/*/briefings/
!/bots/*/memory/
!/.mnemo/
/.mnemo/*
!/.mnemo/extraction-state.json
!/.mnemo/sync/
.DS_Store
*.tmp
"""


@dataclass
class Outcome:
    """What one command did. ``outcome`` is the word the log row carries."""

    op: str
    outcome: str
    message: str
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome in ("ok", "nothing", "busy")


class _Stop(Exception):
    """End the command with this outcome; nothing after it runs."""

    def __init__(self, outcome: str, message: str) -> None:
        self.outcome = outcome
        self.message = message
        super().__init__(message)


@dataclass
class _Tips:
    """The two branch tips, their trees and the hub's format record, read in
    one git call. ``None`` is "there is none"."""

    head: Optional[str] = None
    theirs: Optional[str] = None
    head_tree: Optional[str] = None
    theirs_tree: Optional[str] = None
    format_blob: Optional[str] = None


@dataclass
class _Pulled:
    """What integrating the hub's tip did to the work tree."""

    files: int = 0
    conflicts: int = 0
    losers: int = 0
    shared_changed: bool = False


# --- small pieces ----------------------------------------------------------


def _fs(vault: Path, rel: str) -> Path:
    return Path(vault).joinpath(*rel.split("/"))


def _z(raw: bytes) -> List[str]:
    return [p.decode("utf-8", errors="surrogateescape") for p in raw.split(b"\0") if p]


def _rmtree(path: Path) -> None:
    """Remove a directory git wrote: its object files are read-only on Windows."""

    def _retry(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            func(target)
        except OSError:
            pass

    # `onerror` became `onexc` in 3.12; the package still supports 3.8.
    if sys.version_info >= (3, 12):
        shutil.rmtree(str(path), onexc=_retry)
    else:
        shutil.rmtree(str(path), onerror=_retry)


def _refresh_info(git: Git) -> None:
    """Rewrite git's local attribute and exclude files when they drifted.

    The attributes are what keep git run here by hand from converting a line
    ending; sync's own calls pin that on the command line as well."""
    info = git.git_dir / "info"
    info.mkdir(parents=True, exist_ok=True)
    for name, text in (("attributes", _ATTRIBUTES), ("exclude", _EXCLUDE)):
        target = info / name
        try:
            if target.read_bytes() == text.encode("utf-8"):
                continue
        except OSError:
            pass
        target.write_bytes(text.encode("utf-8"))


def _prepare_repo(git: Git) -> None:
    """Make ``git.work_tree`` a repository on ``main`` that sync owns."""
    if not git.git_dir.exists():
        git.run("-c", "init.defaultBranch=" + BRANCH, "init", "-q")
        try:
            on_main = (git.git_dir / "HEAD").read_text(encoding="utf-8").strip() == "ref: " + LOCAL_REF
        except OSError:
            on_main = False
        if not on_main:  # a git older than init.defaultBranch
            git.run("symbolic-ref", "HEAD", LOCAL_REF)
    (git.git_dir / _MARKER).write_bytes(b"created by `mnemo sync`\n")
    _refresh_info(git)


def _is_ours(git: Git) -> bool:
    return (git.git_dir / _MARKER).is_file()


def _remote_url(git: Git) -> Optional[str]:
    return git.out("config", "--get", "remote.origin.url", check=False) or None


def _shown(url: Optional[str]) -> Optional[str]:
    """A remote as it may be printed: ``user:token@`` never is."""
    from mnemo.core.redact import redact_secrets

    return redact_secrets(url)[0] if url else url


def _tips(git: Git) -> _Tips:
    names = [LOCAL_REF, REMOTE_REF, LOCAL_REF + "^{tree}", REMOTE_REF + "^{tree}",
             f"{REMOTE_REF}:{tracked.FORMAT_FILE}"]
    raw = git.run("cat-file", "--batch-check", stdin=("\n".join(names) + "\n").encode("ascii"))
    found: List[Optional[str]] = []
    for line in raw.stdout.decode("ascii", errors="replace").splitlines():
        parts = line.split()
        found.append(parts[0] if len(parts) == 3 and parts[1] in ("commit", "tree", "blob") else None)
    found += [None] * (len(names) - len(found))
    return _Tips(*found[:len(names)])


def _stage(git: Git, env: Optional[Dict[str, str]] = None) -> str:
    """Make the index hold exactly the tracked files on disk; return its tree."""
    on_disk = set(tracked.walk(git.work_tree))
    indexed = set(_z(git.run("ls-files", "-z", env=env).stdout))
    strangers = sorted(p for p in indexed if not tracked.is_tracked(p))
    paths = sorted((on_disk | indexed).difference(strangers))
    if paths:
        git.run(
            "update-index", "--add", "--remove", "--replace", "-z", "--stdin", env=env,
            stdin=b"".join(p.encode("utf-8", errors="surrogateescape") + b"\0" for p in paths),
        )
    if strangers:
        git.run(
            "update-index", "--force-remove", "-z", "--stdin", env=env,
            stdin=b"".join(p.encode("utf-8", errors="surrogateescape") + b"\0" for p in strangers),
        )
    return git.out("write-tree", env=env)


def _changes(git: Git, old_tree: Optional[str],
             new_tree: Optional[str]) -> Dict[str, Tuple[Optional[str], str]]:
    """``path -> (blob in new_tree or None when deleted, its mode)``. A tree
    of ``None`` is the empty one."""
    if old_tree == new_tree or new_tree is None:
        return {}
    out: Dict[str, Tuple[Optional[str], str]] = {}
    if old_tree is None:
        raw = git.run("ls-tree", "-r", "-z", "--full-tree", new_tree).stdout
        for entry in raw.split(b"\0"):
            meta, tab, path = entry.partition(b"\t")
            fields = meta.decode("ascii", errors="replace").split()
            if tab and len(fields) == 3:
                out[path.decode("utf-8", errors="surrogateescape")] = (fields[2], fields[0])
        return out
    raw = git.run("diff-tree", "-r", "-z", "--raw", "--no-renames", "--no-abbrev",
                  old_tree, new_tree).stdout
    parts = raw.split(b"\0")
    for meta, path in zip(parts[0::2], parts[1::2]):
        fields = meta.decode("ascii", errors="replace").lstrip(":").split()
        if len(fields) < 5:
            continue
        new_mode, new_sha, status = fields[1], fields[3], fields[4]
        rel = path.decode("utf-8", errors="surrogateescape")
        out[rel] = (None if status.startswith("D") else new_sha, new_mode)
    return out


def _fetch(git: Git, timeout: Optional[float]) -> None:
    try:
        git.run("fetch", "--prune", "--no-tags", "-q", "origin",
                timeout=timeout or gitmod.NETWORK_TIMEOUT)
    except GitError as exc:
        raise _Stop("unreachable",
                    f"could not reach the hub ({exc.stderr}); the vault is as it was") from exc


def _check_format(git: Git, state: Dict[str, Any], tips: _Tips) -> None:
    """Stop unless the hub's tip records a format this mnemo knows."""
    if tips.theirs is None:
        state["hubFormatVersion"] = None
        state["hubFormatAt"] = None
        return
    if state.get("hubFormatAt") == tips.theirs:
        version = state.get("hubFormatVersion")  # this tip was read already
    else:
        blob = tips.format_blob
        version = st.parse_format(git.blobs([blob]).get(blob) if blob else None)
    state["hubFormatVersion"] = version
    state["hubFormatAt"] = tips.theirs
    if version is None:
        raise _Stop("format", "the hub records no mnemo sync format: it is not a vault "
                              "`mnemo sync init` published; nothing was pulled or pushed")
    if version != st.FORMAT_VERSION:
        raise _Stop("format", f"the hub is at sync format {version} and this mnemo knows "
                              f"{st.FORMAT_VERSION}: upgrade mnemo on this machine; "
                              "nothing was pulled or pushed")


def _write_file(dest: Path, data: bytes) -> None:
    atomic_write_bytes(dest, data)
    if os.name != "nt":
        try:
            os.chmod(str(dest), 0o644)  # mkstemp's 0600 is not what a page has
        except OSError:
            pass


def _keep_loser(vault: Path, stamp: str, rel: str, data: bytes) -> None:
    dest = _fs(vault, f"{tracked.LOSERS_DIR}/{stamp}/{rel}")
    n = 1
    while dest.exists():
        n += 1
        dest = _fs(vault, f"{tracked.LOSERS_DIR}/{stamp}-{n}/{rel}")
    _write_file(dest, data)


def _trailers(message: str, prefix: str) -> List[str]:
    return [ln[len(prefix):].strip() for ln in message.splitlines() if ln.startswith(prefix)]


def _rebuild_indexes(vault: Path) -> None:
    """A pull wrote ``shared/``: rebuild what is derived from it, as every
    other writer of ``shared/`` does. Best-effort — the pages are already in
    place, and a failed rebuild only delays them to the next session start."""
    from mnemo.core import errors

    try:
        from mnemo.core import rule_activation
        rule_activation.write_index(vault, rule_activation.build_index(vault))
    except Exception as exc:  # noqa: BLE001
        errors.log_error(vault, "sync.rule_activation_index", exc)
    try:
        from mnemo.core.reflex import index as reflex_index
        reflex_index.write_index(vault, reflex_index.build_index(vault))
    except Exception as exc:  # noqa: BLE001
        errors.log_error(vault, "sync.reflex_index", exc)


# --- bringing the hub in ---------------------------------------------------


def _integrate(git: Git, machine: str, state: Dict[str, Any], tips: _Tips) -> _Pulled:
    """Bring the fetched tip into the work tree and move the branch onto it,
    so what still differs from the branch is this machine's own. ``tips`` is
    updated to match."""
    vault = git.work_tree
    done = _Pulled()
    head, theirs = tips.head, tips.theirs
    if theirs is None or head == theirs:
        return done
    base_tree = tips.head_tree
    if head is not None:
        base = git.out("merge-base", head, theirs, check=False) or None
        if base != head:
            # A commit a killed push left behind. Its content is still the
            # work tree; only the branch steps back onto what the hub has.
            if base:
                git.run("update-ref", LOCAL_REF, base)
            else:
                git.run("update-ref", "-d", LOCAL_REF)
            head = base
            base_tree = (tips.theirs_tree if base == theirs
                         else git.out("rev-parse", base + "^{tree}") if base else None)
            tips.head, tips.head_tree = head, base_tree
        if head == theirs:
            return done

    ours = _changes(git, base_tree, _stage(git))
    hubs = _changes(git, base_tree, tips.theirs_tree)
    primary = st.role_of(vault, machine) == st.PRIMARY
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    take: Dict[str, Optional[str]] = {}
    lost: List[Tuple[str, Optional[str]]] = []
    kept: List[str] = []
    for rel, (sha, mode) in hubs.items():
        # The hub cannot widen the set: a path this mnemo would never commit
        # is one it never writes either, whoever pushed it.
        if not tracked.is_tracked(rel) or mode in (_SYMLINK, _GITLINK):
            continue
        mine = ours.get(rel)
        if mine is None:
            take[rel] = sha
        elif mine[0] != sha:
            # Both sides changed it. The primary's version wins everywhere.
            if primary:
                kept.append(rel)
            else:
                lost.append((rel, mine[0]))
                take[rel] = sha

    # What this machine pushed earlier and the primary has since overridden.
    overridden: List[Tuple[str, str]] = []
    if head is not None:
        log = git.out("log", "--format=%B", f"{head}..{theirs}")
        for line in _trailers(log, _OVERRODE_TRAILER):
            who, _, rest = line.partition(" ")
            commit, _, rel = rest.partition(" ")
            if who == machine and commit and rel:
                overridden.append((commit, rel))

    wanted = [sha for sha in take.values() if sha] + [sha for _rel, sha in lost if sha]
    theirs_of = {}
    for commit, rel in overridden:
        sha = git.rev(f"{commit}:{rel}")
        if sha:
            theirs_of[(commit, rel)] = sha
            wanted.append(sha)
    blobs = git.blobs(wanted)

    for rel, sha in lost:
        if sha and sha in blobs:
            _keep_loser(vault, stamp, rel, blobs[sha])
            done.losers += 1
    for key, sha in theirs_of.items():
        if sha in blobs:
            _keep_loser(vault, stamp, key[1], blobs[sha])
            done.losers += 1
    for rel, sha in take.items():
        dest = _fs(vault, rel)
        if sha is None:
            try:
                dest.unlink()
            except FileNotFoundError:
                pass
        else:
            _write_file(dest, blobs[sha])

    for rel in kept:
        # Say whose version lost, so that machine can keep it at its next pull.
        said = _trailers(git.out("log", "-1", "--format=%B", theirs, "--", rel,
                                 env={"GIT_LITERAL_PATHSPECS": "1"}), _MACHINE_TRAILER)
        if said:
            state.setdefault("pendingOverrides", []).append(
                {"machine": said[0], "commit": theirs, "path": rel})

    # The index is left as it was: every reader of it restages first.
    git.run("update-ref", LOCAL_REF, theirs)
    tips.head, tips.head_tree = theirs, tips.theirs_tree

    done.files = len(take)
    done.conflicts = len(kept) + len(lost) + len(overridden)
    done.shared_changed = any(rel.startswith("shared/") for rel in take)
    state["conflictsResolved"] = int(state.get("conflictsResolved") or 0) + done.conflicts
    state["losersKept"] = int(state.get("losersKept") or 0) + done.losers
    if done.shared_changed:
        _rebuild_indexes(vault)
    return done


# --- the secret gate -------------------------------------------------------


def _scan(git: Git, publishing: Dict[str, Tuple[Optional[str], str]]) -> List[Dict[str, str]]:
    """Every secret shape in the files a push is about to publish: the file
    and the kind, never the value."""
    from mnemo.core.redact import find

    by_sha: Dict[str, List[str]] = {}
    for rel, (sha, _mode) in publishing.items():
        if sha:
            by_sha.setdefault(sha, []).append(rel)
    hits = set()
    blobs = git.blobs(list(by_sha))
    for sha, rels in by_sha.items():
        text = blobs.get(sha, b"").decode("utf-8", errors="replace")
        for kind in {f.kind for f in find(text)}:
            for rel in rels:
                hits.add((rel, kind))
    return [{"path": rel, "kind": kind} for rel, kind in sorted(hits)]


def _refusal(found: List[Dict[str, str]]) -> str:
    files = sorted({f["path"] for f in found})
    lines = [f"push refused: {len(found)} secret shape{'s' if len(found) != 1 else ''} in "
             f"{len(files)} file{'s' if len(files) != 1 else ''} it would publish "
             "(values are not shown)"]
    for rel in files:
        kinds = ", ".join(f["kind"] for f in found if f["path"] == rel)
        lines.append(f"  {rel}: {kinds}")
    lines.append("  → take them out, then push again. `mnemo redact --apply` rewrites pages "
                 "and briefings.")
    if any(rel.startswith("bots/") and "/memory/" in rel for rel in files):
        lines.append("  → a file under bots/*/memory/ is a copy of Claude Code's own memory: "
                     "edit the original, or the next session start copies it back")
    return "\n".join(lines)


# --- one push, already holding the lock ------------------------------------


def _push(git: Git, machine: str, state: Dict[str, Any], config_path: Optional[Path],
          timeout: Optional[float]) -> Tuple[str, str, Dict[str, Any]]:
    """``(outcome, message, detail)``; the detail is the log row's ``files``,
    the ``conflicts`` the pulls on the way resolved and, once sent, the
    ``commit`` the hub took."""
    vault = git.work_tree
    said = ""
    conflicts = 0
    for _attempt in range(PUSH_ATTEMPTS):
        _fetch(git, timeout)
        tips = _tips(git)
        _check_format(git, state, tips)
        conflicts += _integrate(git, machine, state, tips).conflicts
        if st.role_of(vault, machine) == st.PRIMARY:
            st.write_format(vault)
            st.write_seed(vault, config_path)
        tree = _stage(git)
        head, head_tree = tips.head, tips.head_tree
        if head is not None and tree == head_tree and tips.theirs == head:
            state["refused"] = []
            return "nothing", "nothing to push", {"files": 0, "conflicts": conflicts}

        publishing = _changes(git, tips.theirs_tree, tree)
        found = _scan(git, publishing)
        if found:
            state["refused"] = found
            state["pushesRefused"] = int(state.get("pushesRefused") or 0) + 1
            return "refused", _refusal(found), {"files": 0, "conflicts": conflicts}

        commit = head
        if head is None or tree != head_tree:
            pending = [o for o in state.get("pendingOverrides") or [] if isinstance(o, dict)]
            lines = [f"mnemo sync: {machine} ({len(publishing)} file"
                     f"{'s' if len(publishing) != 1 else ''})", "", _MACHINE_TRAILER + machine]
            lines += [f"{_OVERRODE_TRAILER}{o.get('machine')} {o.get('commit')} {o.get('path')}"
                      for o in pending]
            args = ["commit-tree", tree, "-m", "\n".join(lines)]
            if head is not None:
                args += ["-p", head]
            commit = git.out(*args)
            git.run("update-ref", LOCAL_REF, commit)
        sent = git.run("push", "-q", "origin", f"{LOCAL_REF}:{LOCAL_REF}", check=False,
                       timeout=timeout or gitmod.NETWORK_TIMEOUT)
        if sent.returncode == 0:
            state["refused"] = []
            state["pendingOverrides"] = []
            n = len(publishing)
            return ("ok", f"pushed {n} file{'s' if n != 1 else ''}",
                    {"files": n, "conflicts": conflicts, "commit": commit})
        # The hub moved first, or would not take it. Step back off the commit
        # (the work tree still holds everything) and look again.
        said = gitmod._tail(sent.stderr)
        if head is not None:
            git.run("update-ref", LOCAL_REF, head)
        else:
            git.run("update-ref", "-d", LOCAL_REF)
    return ("error", f"the hub did not take the push after {PUSH_ATTEMPTS} tries ({said})",
            {"files": 0, "conflicts": conflicts})


# --- the commands ----------------------------------------------------------


def _finish(vault: Path, machine: str, op: str, timer: st.Timer, state: Optional[Dict[str, Any]],
            outcome: str, message: str, **detail: Any) -> Outcome:
    """Record one run: the state it leaves, and its row in the log."""
    now = st.now_iso()
    if state is not None:
        if outcome in ("ok", "nothing"):
            if op in ("pull", "join", "init", "push"):
                state["lastPull"] = now
            if op in ("push", "init"):
                state["lastPush"] = now
            last = state.get("lastError")
            if op != "pull" or not isinstance(last, dict) or last.get("op") == "pull":
                state["lastError"] = None
        elif outcome not in ("busy", "off"):
            state["lastError"] = {"at": now, "op": op, "outcome": outcome,
                                  "message": message.splitlines()[0]}
        st.save(vault, state)
        health.drop_cache(vault)
    if outcome != "off":
        st.append_log(vault, {"at": now, "op": op, "outcome": outcome, "machine": machine,
                              "durationMs": timer.ms(), **detail})
    return Outcome(op, outcome, message, dict(detail))


def _off(op: str) -> Outcome:
    return Outcome(op, "off", "sync is off for this vault: run `mnemo sync init <remote>` on the "
                              "machine that owns it, or `mnemo sync join <remote>` on another")


def _guarded(vault: Path, machine: str, op: str, timer: st.Timer, state: Dict[str, Any],
             body, extra: Optional[Dict[str, Any]] = None) -> Outcome:
    """Run ``body`` and turn every way it can end into one recorded outcome.
    ``extra`` is carried into the log row whatever the outcome."""
    extra = dict(extra or {})
    try:
        outcome, message, detail = body()
    except _Stop as stop:
        return _finish(vault, machine, op, timer, state, stop.outcome, stop.message, **extra)
    except GitMissing:
        raise
    except GitTimeout as exc:
        return _finish(vault, machine, op, timer, state, "unreachable",
                       f"could not reach the hub ({exc.stderr}); the vault is as it was", **extra)
    except (GitError, OSError) as exc:
        text = exc.stderr if isinstance(exc, GitError) else str(exc)
        return _finish(vault, machine, op, timer, state, "error", f"{op} failed: {text}", **extra)
    return _finish(vault, machine, op, timer, state, outcome, message, **extra, **detail)


def pull(vault: Path, *, machine: str, timeout: Optional[float] = None,
         trigger: Optional[str] = None) -> Outcome:
    """Bring the hub into the vault. Never leaves it conflicted.

    ``trigger`` says what ran it — ``cli``, ``session_start`` — and lands in
    the log row as ``trigger``, for ``tools/measure_sync.py`` (#644).
    """
    vault = Path(vault)
    timer = st.Timer()
    if not st.enabled(vault):
        return _off("pull")
    with locks.try_lock(st.lock_path(vault), stale_after=_LOCK_STALE_SECONDS) as held:
        if not held:
            return _finish(vault, machine, "pull", timer, None, "busy",
                           "busy: another `mnemo sync` is running on this vault")
        state = st.load(vault)
        git = Git(vault)

        def body():
            _refresh_info(git)
            _fetch(git, timeout)
            tips = _tips(git)
            _check_format(git, state, tips)
            done = _integrate(git, machine, state, tips)
            message = (f"pulled {done.files} file{'s' if done.files != 1 else ''}"
                       if done.files else "already up to date")
            if done.conflicts:
                message += (f"; {done.conflicts} conflict{'s' if done.conflicts != 1 else ''} "
                            f"resolved for the primary, {done.losers} losing "
                            f"version{'s' if done.losers != 1 else ''} kept under "
                            f"{tracked.LOSERS_DIR}/")
            return "ok", message, {"files": done.files, "conflicts": done.conflicts}

        return _guarded(vault, machine, "pull", timer, state, body,
                        {"trigger": trigger} if trigger else None)


def push(vault: Path, *, machine: str, config_path: Optional[Path] = None,
         timeout: Optional[float] = None, trigger: Optional[str] = None) -> Outcome:
    """Commit what changed in the tracked set and push it, pulling first.

    ``trigger`` says what ran it — ``cli``, ``session_start``, ``session_end``
    — and lands in the log row as ``trigger``, for ``tools/measure_sync.py``
    (#644): a briefing whose push ran at a SessionStart is one that waited.
    """
    vault = Path(vault)
    timer = st.Timer()
    if not st.enabled(vault):
        return _off("push")
    with locks.try_lock(st.lock_path(vault), stale_after=_LOCK_STALE_SECONDS) as held:
        if not held:
            return _finish(vault, machine, "push", timer, None, "busy",
                           "busy: another `mnemo sync` is running on this vault")
        state = st.load(vault)
        git = Git(vault)

        def body():
            _refresh_info(git)
            return _push(git, machine, state, config_path, timeout)

        return _guarded(vault, machine, "push", timer, state, body,
                        {"trigger": trigger} if trigger else None)


def _pin_machine_id(config_path: Optional[Path], machine: str) -> None:
    """Write the id into this machine's config once, so a hostname that
    changes with the network cannot change which machine is the primary."""
    if config_path is None:
        return
    from mnemo.core import config as cfg_mod

    raw = st._read_json(Path(config_path))
    section = raw.get("sync")
    if isinstance(section, dict) and section.get("machineId"):
        return
    cfg_mod.set_config_value("sync.machineId", machine, Path(config_path))


def init(vault: Path, remote: str, *, machine: str, config_path: Optional[Path] = None,
         timeout: Optional[float] = None) -> Outcome:
    """Make this machine's vault the primary of ``remote`` and push it."""
    vault = Path(vault)
    timer = st.Timer()
    if not vault.is_dir():
        return Outcome("init", "error", f"there is no vault at {vault}: run `mnemo init` first")
    git = Git(vault)
    created = not git.git_dir.exists()
    if not created and not _is_ours(git):
        return Outcome("init", "error",
                       f"{vault} is already a git repository that `mnemo sync` did not create; "
                       "sync commits only its own tracked set, so it will not take over a "
                       "history it does not own. Move that .git aside first.")
    recorded = st.primary_of(vault)
    if recorded is not None and recorded != machine:
        return Outcome("init", "error",
                       f"this vault's primary is '{recorded}' and this machine is '{machine}': "
                       "nothing here changes the primary after `init`")

    with locks.try_lock(st.lock_path(vault), stale_after=_LOCK_STALE_SECONDS) as held:
        if not held:
            return _finish(vault, machine, "init", timer, None, "busy",
                           "busy: another `mnemo sync` is running on this vault")
        state = st.load(vault)

        def body():
            _prepare_repo(git)
            current = _remote_url(git)
            if current is None:
                git.run("remote", "add", "origin", remote)
            elif current != remote:
                if git.rev(LOCAL_REF) is not None:
                    raise _Stop("error", f"this vault already syncs with {_shown(current)}; "
                                         "changing hubs is not something `init` does")
                git.run("remote", "set-url", "origin", remote)
            _fetch(git, timeout)
            tips = _tips(git)
            if tips.theirs is not None and tips.head is None:
                raise _Stop("error", "that hub already holds a vault: run `mnemo sync join "
                                     "<remote>` on this machine instead")
            _check_format(git, state, tips)
            _pin_machine_id(config_path, machine)
            if st.primary_of(vault) is None:
                st.write_primary(vault, machine)
            st.write_format(vault)
            st.write_seed(vault, config_path)
            state["enabled"] = True
            outcome, message, detail = _push(git, machine, state, config_path, timeout)
            if outcome == "nothing":
                return "ok", f"already initialised as the primary '{machine}'; nothing to push", detail
            if outcome == "ok":
                message = f"this machine ('{machine}') is the primary of {_shown(remote)}; {message}"
            return outcome, message, detail

        result = _guarded(vault, machine, "init", timer, state, body)
    if created and not state.get("enabled"):
        # Nothing was published and sync is not on: leave no half-made
        # repository behind for the next command to trip over.
        _rmtree(git.git_dir)
    return result


def _seed_config(vault: Path, config_path: Optional[Path], machine: str,
                 previous: Dict[str, Any]) -> Optional[str]:
    """Write this machine's config from the seed, once. Returns what to say."""
    if config_path is None:
        return None
    config_path = Path(config_path)
    if config_path.exists():
        # It lives outside the vault and survived the move: it is this
        # machine's own, and a seed never overwrites one.
        _pin_machine_id(config_path, machine)
        return f"config kept as it was: {config_path}"
    seeded = dict(st.read_seed(vault))
    seeded["vaultRoot"] = str(vault)
    old = previous.get("sync") if isinstance(previous.get("sync"), dict) else {}
    section = dict(seeded.get("sync") or {})
    section["machineId"] = old.get("machineId") or machine
    seeded["sync"] = section
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(seeded, indent=2), encoding="utf-8")
    return f"config written once from the primary's seed: {config_path}"


def join(vault: Path, remote: str, *, machine: str, config_path: Optional[Path] = None,
         timeout: Optional[float] = None) -> Outcome:
    """Put the hub's vault where this machine's vault is, moving aside
    whatever was there."""
    vault = Path(vault)
    timer = st.Timer()
    if st.enabled(vault) and _remote_url(Git(vault)) == remote:
        return Outcome("join", "ok", f"already joined: {vault} syncs with {_shown(remote)}")
    parent = vault.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{vault.name}.sync-join-{os.getpid()}"
    with locks.try_lock(parent / f".{vault.name}.sync-join.lock",
                        stale_after=_LOCK_STALE_SECONDS) as held:
        if not held:
            return Outcome("join", "busy", "busy: another `mnemo sync join` is running here")
        state: Dict[str, Any] = {}
        try:
            if staging.exists():
                _rmtree(staging)
            staging.mkdir()
            git = Git(staging)
            _prepare_repo(git)
            git.run("remote", "add", "origin", remote)
            _fetch(git, timeout)
            tips = _tips(git)
            if tips.theirs is None:
                raise _Stop("error", "that hub is empty: run `mnemo sync init <remote>` on the "
                                     "machine that owns the vault first")
            _check_format(git, state, tips)
            _integrate(git, machine, state, tips)
            primary = st.primary_of(staging)
            if primary is None:
                raise _Stop("error", "that hub names no primary: it is not a vault "
                                     "`mnemo sync init` published")

            previous = st._read_json(Path(config_path)) if config_path is not None else {}
            aside: Optional[Path] = None
            if vault.exists():
                if vault.is_dir() and not any(vault.iterdir()):
                    vault.rmdir()
                else:
                    stamp = time.strftime("%Y%m%dT%H%M%S")
                    aside = parent / f"{vault.name}.pre-sync-{stamp}"
                    n = 1
                    while aside.exists():
                        n += 1
                        aside = parent / f"{vault.name}.pre-sync-{stamp}-{n}"
                    os.rename(str(vault), str(aside))
            try:
                os.rename(str(staging), str(vault))
            except OSError:
                if aside is not None:
                    os.rename(str(aside), str(vault))
                raise
        except _Stop as stop:
            return Outcome("join", stop.outcome, stop.message)
        except GitTimeout as exc:
            return Outcome("join", "unreachable",
                           f"could not reach the hub ({exc.stderr}); the vault is as it was")
        except (GitError, OSError) as exc:
            text = exc.stderr if isinstance(exc, GitError) else str(exc)
            return Outcome("join", "error", f"join failed: {text}; the vault is as it was")
        finally:
            if staging.exists():
                _rmtree(staging)

        role = st.role_of(vault, machine)
        lines = [f"joined {_shown(remote)} as {'the primary' if role == st.PRIMARY else 'a satellite'}"
                 f" '{machine}' (primary: '{primary}')"]
        if role == st.PRIMARY:
            lines.append("  ! this machine's id is the one the hub names as its primary. If this "
                         "is a second machine, set `sync.machineId` in its config and join again.")
        if aside is not None:
            lines.append(f"  the vault that was here is now at {aside}")
        said = _seed_config(vault, config_path, machine, previous)
        if said:
            lines.append(f"  {said}")
        state["enabled"] = True
        return _finish(vault, machine, "join", timer, state, "ok", "\n".join(lines),
                       movedAside=str(aside) if aside is not None else None)


def _pending(git: Git, head_tree: Optional[str]) -> int:
    """How many tracked files differ from the branch, without touching the
    index a push will use."""
    scratch = git.git_dir / f"mnemo-sync-status-index-{os.getpid()}"
    try:
        try:
            shutil.copyfile(str(git.git_dir / "index"), str(scratch))
        except OSError:
            pass
        tree = _stage(git, env={"GIT_INDEX_FILE": str(scratch)})
        return len(_changes(git, head_tree, tree))
    finally:
        for leftover in (scratch, scratch.with_name(scratch.name + ".lock")):
            try:
                leftover.unlink()
            except OSError:
                pass


def status(vault: Path, *, machine: str) -> Dict[str, Any]:
    """What sync is doing on this machine. Reads the hub as last fetched: it
    never talks to it."""
    vault = Path(vault)
    if not st.enabled(vault):
        return {"enabled": False, "machineId": machine}
    state = st.load(vault)
    git = Git(vault)
    tips = _tips(git)
    ahead = behind = 0
    if tips.head and tips.theirs and tips.head != tips.theirs:
        counts = git.out("rev-list", "--left-right", "--count", f"{tips.head}...{tips.theirs}").split()
        if len(counts) == 2:
            ahead, behind = int(counts[0]), int(counts[1])

    hub_format = state.get("hubFormatVersion")
    last_error = state.get("lastError") if isinstance(state.get("lastError"), dict) else None
    return {
        "enabled": True,
        "remote": _shown(_remote_url(git)),
        "role": st.role_of(vault, machine),
        "machineId": machine,
        "primary": st.primary_of(vault),
        "ahead": ahead,
        "behind": behind,
        "pendingFiles": _pending(git, tips.head_tree),
        "lastPull": state.get("lastPull"),
        "lastPullAgoSeconds": st.seconds_since(state.get("lastPull")),
        "lastPush": state.get("lastPush"),
        "lastPushAgoSeconds": st.seconds_since(state.get("lastPush")),
        "conflictsResolved": int(state.get("conflictsResolved") or 0),
        "losersKept": int(state.get("losersKept") or 0),
        "pushesRefused": int(state.get("pushesRefused") or 0),
        "refused": [r for r in state.get("refused") or [] if isinstance(r, dict)],
        "lastError": last_error,
        "formatVersion": st.FORMAT_VERSION,
        "hubFormatVersion": hub_format,
        "formatSupported": hub_format is None or hub_format == st.FORMAT_VERSION,
    }

