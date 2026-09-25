"""Remove a dispatched child's worktree once its PR has merged (#503).

Until this module, nothing did. ``dispatch.remove_worktree`` runs only on the
rollback path of a failed spawn, and ``landing`` removes only its own
rehearsal tree. On 2026-09-25, 13 trees across meunu and mnemo-desktop had a
merged PR, a clean status and nothing unpushed, and were still on disk. The
2026-09-16 spec left removal as "a later, separate decision". The maintainer
made that decision on 2026-09-25: the tree goes, with no manual step.

**Where it runs.** On a session start, not on the child's SessionEnd and not
in ``pr-follow``. Both of those depend on the child's SessionEnd, and #502
measured it missing for 22 of 95 self-stopped children. ``pr-follow`` also
follows only children that were granted ``push``. The hook itself costs one
ledger read. At most once every :data:`MIN_INTERVAL_SECONDS` per repo it
spawns ``mnemo tree-sweep`` detached, and that process makes the ``git`` and
``gh`` calls. So a burst of session starts, like the 42 in the 2026-09-15
hook storm, spawns one sweep.

**Which trees.** Only paths that ``dispatch.issue_for_cwd`` recognises as
dispatch paths, as ``git worktree list`` reports them for the repo. A hand-made
worktree is never looked at. Removal needs every one of these:

- **The branch's PR has merged.** ``gh pr list --head <branch> --state all``
  returns a ``MERGED`` PR and no ``OPEN`` one. A tree with no PR at all is
  left alone without comment. That covers a twin that lost: ``dispatch
  --twins`` gives it no PR and no push, so its commits exist only in its
  tree, and removing it would lose them. A twin whose PR merged is removed
  like any other child's tree.
- **The child is gone.** Every job whose ``cwd`` is the tree must be
  ``stopped``, not blocked, not live on the daemon roster, and last updated
  more than :data:`GRACE_SECONDS` ago. The last condition gives the
  SessionEnd workers the stop started time to finish reading the tree (#247).
  A ``working``, ``done`` or blocked child keeps its tree. A tree with no job
  on disk (``claude rm``) counts as gone.
- **Nothing else is in it.** No process has its cwd inside the tree. The
  check reads ``lsof -d cwd``, which took 0.17 s over 348 processes. On
  2026-09-25 a ``mnemo resume --watch`` was running from a merged meunu tree.
  Without ``lsof``, as on Windows, this check is skipped.
- **Nothing is lost.** The status is clean, untracked files included. No
  commit is ahead of the upstream. The tip is contained in the merged PR's
  ``headRefOid``. A squash merge leaves the tip outside the base branch, so
  the base is the wrong reference. The PR's own head is what merged.

The tree is removed with ``git worktree remove`` and no ``--force``, so git
makes its own last check. The branch is then deleted with ``-d``, never
``-D``, for the reason given in ``dispatch.remove_worktree``. ``-d`` compares
the branch with its upstream. On meunu, all ten merged trees still had
``origin/<branch>`` at the tip, so ``-d`` succeeds. If the tracking ref has
been pruned, ``-d`` refuses. The branch is kept and the refusal is reported.

**How the maintainer hears it.** A tree that would have been removed but
holds work (dirty, unpushed, not in the PR, or ``-d`` refused) is reported
once per reason. The report is a line in the repo's day log, plus a notice to
the session that dispatched the child if it is still live. A removal is
logged the same way. The ledger (:data:`LEDGER_NAME`) remembers what was
already said, so the next sweep says nothing new. ``mnemo sessions`` already
hides a finished child whose tree is gone (#292), so a removal also clears
that row from the queue.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from mnemo.core import locks

#: A repo is swept at most this often. A PR merged a minute ago waits at most
#: this long, which is cheap next to the days the trees sat there before.
MIN_INTERVAL_SECONDS = 30 * 60
#: How long a stopped child's job must have been quiet. SessionEnd's workers
#: are detached and read the transcript, and #247 is what happens when the tree
#: goes first.
GRACE_SECONDS = 15 * 60

LEDGER_NAME = "tree-sweep.json"
LOCK_NAME = "tree-sweep.lock"
LEDGER_LOCK_NAME = "tree-sweep-ledger.lock"
LOCK_STALE_SECONDS = 10 * 60

#: What happened to one tree. ``REMOVED`` and ``BRANCH_KEPT`` removed the
#: tree. The ``HOLDS_*`` reasons, and ``UNVERIFIABLE``, apply to a merged
#: PR's tree that still holds work, and the maintainer is told about them.
#: The rest are waits, and are not reported.
REMOVED = "removed"
BRANCH_KEPT = "removed-branch-kept"
HOLDS_DIRTY = "dirty"
HOLDS_UNPUSHED = "unpushed"
HOLDS_NOT_IN_PR = "not-in-pr"
UNVERIFIABLE = "unverifiable"
REMOVE_FAILED = "remove-failed"
CHILD_RUNNING = "child-running"
IN_USE = "in-use"
OPEN_PR = "open-pr"
NOT_MERGED = "not-merged"
DETACHED = "detached"

#: Outcomes the maintainer hears about.
TOLD = frozenset({
    REMOVED, BRANCH_KEPT, HOLDS_DIRTY, HOLDS_UNPUSHED, HOLDS_NOT_IN_PR,
    UNVERIFIABLE, REMOVE_FAILED,
})

_WHY = {
    HOLDS_DIRTY: "it has uncommitted or untracked changes",
    HOLDS_UNPUSHED: "it has commits its upstream does not",
    HOLDS_NOT_IN_PR: "its branch has commits the merged PR does not contain",
    UNVERIFIABLE: "git could not say whether the merged PR contains its branch",
    REMOVE_FAILED: "`git worktree remove` refused",
}

Runner = Callable[..., subprocess.CompletedProcess]


def _run(args: Sequence[str], *, cwd: Path | str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(args), cwd=str(cwd), capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(list(args), 1, "", str(exc))


@dataclass(frozen=True)
class Tree:
    path: Path
    branch: Optional[str]


@dataclass
class Verdict:
    tree: Path
    branch: Optional[str]
    outcome: str
    pr: Optional[str] = None
    detail: str = ""

    @property
    def removed(self) -> bool:
        return self.outcome in (REMOVED, BRANCH_KEPT)


@dataclass
class SweepReport:
    verdicts: List[Verdict] = field(default_factory=list)
    locked: bool = False


# ---------------------------------------------------------------------------
# reading git, gh, the jobs dir and the process table
# ---------------------------------------------------------------------------


def dispatch_trees(repo_root: Path | str, *, run: Runner = _run) -> List[Tree]:
    """Every dispatch worktree of *repo_root*, with the branch it has out."""
    from mnemo.core.dispatch import issue_for_cwd

    result = run(["git", "worktree", "list", "--porcelain"], cwd=repo_root)
    if result.returncode != 0:
        return []
    out: List[Tree] = []
    path: Optional[str] = None
    branch: Optional[str] = None
    for line in (result.stdout or "").splitlines() + [""]:
        if line.startswith("worktree "):
            path, branch = line[len("worktree "):], None
        elif line.startswith("branch refs/heads/"):
            branch = line[len("branch refs/heads/"):]
        elif not line.strip():
            if path and issue_for_cwd(path) is not None:
                out.append(Tree(Path(path), branch))
            path, branch = None, None
    return out


def merged_pr(branch: str, *, repo_root: Path | str, run: Runner = _run
              ) -> tuple:
    """``(state, prs)`` for *branch*'s PRs.

    ``state`` is ``"open"`` when any PR from the branch is open, ``"merged"``
    when at least one merged and none is open, and ``"none"`` otherwise,
    including when ``gh`` could not answer. ``prs`` lists the merged ones as
    ``(label, headRefOid)``.
    """
    result = run(
        ["gh", "pr", "list", "--head", branch, "--state", "all",
         "--json", "number,state,headRefOid", "--limit", "20"],
        cwd=repo_root,
    )
    if result.returncode != 0:
        return "none", []
    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError:
        return "none", []
    if not isinstance(rows, list):
        return "none", []
    merged = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("state") == "OPEN":
            return "open", []
        if row.get("state") == "MERGED" and row.get("headRefOid"):
            merged.append((f"#{row.get('number')}", str(row["headRefOid"])))
    return ("merged" if merged else "none"), merged


def _parse_when(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def child_gone(tree: Path, sessions: Sequence[Any], *, now: float) -> bool:
    """True when no session that ran in *tree* can still need it."""
    from mnemo.core.sessions.jobs import normalize_cwd

    wanted = normalize_cwd(str(tree))
    for s in sessions:
        if normalize_cwd(getattr(s, "cwd", None)) != wanted:
            continue
        if getattr(s, "state", None) != "stopped":
            return False
        if getattr(s, "is_blocked", False) or getattr(s, "live", None) is True:
            return False
        stamp = _parse_when(getattr(s, "updated_at", None))
        if stamp is None or now - stamp < GRACE_SECONDS:
            return False
    return True


def process_cwds(*, run: Runner = _run) -> Optional[List[str]]:
    """Every process's working directory, or ``None`` when it cannot be read."""
    result = run(["lsof", "-w", "-d", "cwd", "-Fn"], cwd=os.path.expanduser("~"))
    if result.returncode not in (0, 1) or not result.stdout:
        return None
    return [line[1:] for line in result.stdout.splitlines() if line.startswith("n")]


def in_use(tree: Path, cwds: Optional[Sequence[str]]) -> bool:
    if not cwds:
        return False
    root = os.path.realpath(str(tree))
    for cwd in cwds:
        real = os.path.realpath(cwd)
        if real == root or real.startswith(root + os.sep):
            return True
    return False


def _git_ok(args: Sequence[str], *, cwd: Path, run: Runner) -> Optional[str]:
    result = run(["git", *args], cwd=cwd)
    return result.stdout.strip() if result.returncode == 0 else None


def holds_work(tree: Tree, merged: Sequence[tuple], *, run: Runner = _run
               ) -> Optional[str]:
    """Why *tree* cannot go without losing something, or ``None``."""
    status = run(["git", "status", "--porcelain"], cwd=tree.path)
    if status.returncode != 0:
        return UNVERIFIABLE
    if status.stdout.strip():
        return HOLDS_DIRTY
    ahead = _git_ok(["rev-list", "--count", "@{upstream}..HEAD"], cwd=tree.path, run=run)
    if ahead is not None and ahead != "0":
        return HOLDS_UNPUSHED
    tip = _git_ok(["rev-parse", "HEAD"], cwd=tree.path, run=run)
    if not tip:
        return UNVERIFIABLE
    unknown = False
    for _label, head in merged:
        check = run(["git", "merge-base", "--is-ancestor", tip, head], cwd=tree.path)
        if check.returncode == 0:
            return None
        if check.returncode != 1:
            unknown = True
    return UNVERIFIABLE if unknown else HOLDS_NOT_IN_PR


def remove(tree: Tree, *, repo_root: Path | str, run: Runner = _run) -> tuple:
    """Remove *tree*, then its branch with ``-d``. ``(outcome, detail)``."""
    gone = run(["git", "worktree", "remove", str(tree.path)], cwd=repo_root)
    if gone.returncode != 0:
        return REMOVE_FAILED, (gone.stderr or gone.stdout or "").strip()
    if not tree.branch:
        return REMOVED, ""
    deleted = run(["git", "branch", "-d", tree.branch], cwd=repo_root)
    if deleted.returncode != 0:
        return BRANCH_KEPT, (deleted.stderr or deleted.stdout or "").strip()
    return REMOVED, ""


# ---------------------------------------------------------------------------
# one pass
# ---------------------------------------------------------------------------


def judge(tree: Tree, *, repo_root: Path | str, sessions: Sequence[Any],
          cwds: Optional[Sequence[str]], now: float, run: Runner = _run,
          skip: Optional[str] = None) -> Verdict:
    """Decide about one tree and, when every check passes, remove it."""
    def verdict(outcome: str, pr: Optional[str] = None, detail: str = "") -> Verdict:
        return Verdict(tree.path, tree.branch, outcome, pr, detail)

    if not tree.branch:
        return verdict(DETACHED)
    if skip and in_use(tree.path, [skip]):
        return verdict(IN_USE)
    state, merged = merged_pr(tree.branch, repo_root=repo_root, run=run)
    if state == "open":
        return verdict(OPEN_PR)
    if state != "merged":
        return verdict(NOT_MERGED)
    pr = ", ".join(label for label, _ in merged)
    if not child_gone(tree.path, sessions, now=now):
        return verdict(CHILD_RUNNING, pr)
    if in_use(tree.path, cwds):
        return verdict(IN_USE, pr)
    why = holds_work(tree, merged, run=run)
    if why:
        return verdict(why, pr)
    outcome, detail = remove(tree, repo_root=repo_root, run=run)
    return verdict(outcome, pr, detail)


def _read_sessions() -> List[Any]:
    from mnemo.core.sessions.jobs import read_sessions

    return list(read_sessions())


def sweep(
    cfg: Optional[Dict[str, Any]],
    *,
    vault_root: Path,
    repo_root: Path | str,
    now: Optional[float] = None,
    run: Runner = _run,
    reader: Optional[Callable[[], List[Any]]] = None,
    cwds: Optional[Callable[[], Optional[List[str]]]] = None,
    tell: Optional[Callable[[Verdict], None]] = None,
    skip: Optional[str] = None,
) -> SweepReport:
    """Judge every dispatch tree of *repo_root*, one pass, behind a lock.

    *skip* is a directory whose tree is left alone this pass, the cwd of the
    session that started the sweep. Never raises past one tree.
    """
    report = SweepReport()
    moment = time.time() if now is None else now
    lock = Path(vault_root) / ".mnemo" / LOCK_NAME
    with locks.try_lock(lock, stale_after=LOCK_STALE_SECONDS) as held:
        if not held:
            report.locked = True
            return report
        trees = dispatch_trees(repo_root, run=run)
        if not trees:
            return report
        sessions = (reader or _read_sessions)()
        table = (cwds or (lambda: process_cwds(run=run)))()
        say = tell or (lambda v: _tell(cfg, vault_root, v, sessions))
        for tree in trees:
            try:
                v = judge(tree, repo_root=repo_root, sessions=sessions,
                          cwds=table, now=moment, run=run, skip=skip)
            except Exception as exc:  # noqa: BLE001 — one tree must not cost the rest
                v = Verdict(tree.path, tree.branch, UNVERIFIABLE, detail=str(exc))
            report.verdicts.append(v)
            if _first_time(vault_root, v):
                say(v)
    return report


# ---------------------------------------------------------------------------
# telling the maintainer
# ---------------------------------------------------------------------------


def render(v: Verdict) -> str:
    name = v.tree.name
    pr = f" (PR {v.pr} merged)" if v.pr else ""
    if v.outcome == REMOVED:
        return f"🧹 removed {name} and its branch {v.branch}{pr}"
    if v.outcome == BRANCH_KEPT:
        return (f"🧹 removed {name}{pr}; kept branch {v.branch}: "
                f"`git branch -d` refused ({v.detail or 'not fully merged'})")
    why = _WHY.get(v.outcome, v.outcome)
    tail = f": {v.detail}" if v.detail else ""
    return f"🧹 kept {name}{pr}: {why}{tail}"


def ledger_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / LEDGER_NAME


def load_ledger(vault_root: Path) -> Dict[str, Any]:
    try:
        data = json.loads(ledger_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _update(vault_root: Path, change: Callable[[Dict[str, Any]], None]) -> None:
    from mnemo.core import atomic

    lock = Path(vault_root) / ".mnemo" / LEDGER_LOCK_NAME
    with locks.try_lock(lock, stale_after=30.0) as held:
        if not held:
            return
        data = load_ledger(vault_root)
        change(data)
        path = ledger_path(vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.atomic_write_bytes(
            path, json.dumps(data, indent=2, sort_keys=True).encode("utf-8"))


def _first_time(vault_root: Path, v: Verdict) -> bool:
    """Record *v* and say whether it is news. Waits are never news."""
    if v.outcome not in TOLD:
        return False
    key = str(v.tree)
    said = (load_ledger(vault_root).get("said") or {}).get(key)
    if said == v.outcome and not v.removed:
        return False
    news = [False]

    def change(data: Dict[str, Any]) -> None:
        seen = data.setdefault("said", {})
        if not isinstance(seen, dict):
            seen = data["said"] = {}
        if v.removed:
            seen.pop(key, None)
            news[0] = True
        elif seen.get(key) != v.outcome:
            seen[key] = v.outcome
            news[0] = True

    _update(vault_root, change)
    return news[0]


def _tell(cfg, vault_root: Path, v: Verdict, sessions: Sequence[Any]) -> None:
    text = render(v)
    try:
        from mnemo.core import agent as agent_mod
        from mnemo.core import log_writer
        from mnemo.core.sessions.jobs import _repo_root

        name = agent_mod.resolve_canonical_agent(
            _repo_root(str(v.tree)) or str(v.tree)).name
        log_writer.append_line(name, text, cfg or {})
    except Exception:
        pass
    try:
        from mnemo.core.sessions import inbox, parents
        from mnemo.core.sessions.jobs import normalize_cwd

        wanted = normalize_cwd(str(v.tree))
        links = parents.read(vault_root)
        told = set()
        for s in sessions:
            if normalize_cwd(getattr(s, "cwd", None)) != wanted:
                continue
            parent = links.get(getattr(s, "short_id", ""))
            if parent and parent not in told:
                told.add(parent)
                inbox.notify(vault_root, parent, text)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# the hook trigger
# ---------------------------------------------------------------------------


def enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    raw = ((cfg or {}).get("dispatch") or {}).get("removeMergedTrees", True)
    return raw if isinstance(raw, bool) else True


def _spawn_sweep(cwd: Optional[str] = None) -> None:
    """``mnemo tree-sweep``, detached, through the hooks' one chokepoint."""
    from mnemo.hooks.session_start import _spawn_detached

    _spawn_detached(["tree-sweep"], cwd=cwd)


def on_session_start(cfg: Optional[Dict[str, Any]], *, vault_root: Path,
                     cwd: Optional[str],
                     spawn: Optional[Callable[[Optional[str]], None]] = None,
                     now: Optional[float] = None) -> str:
    """Start a sweep of *cwd*'s repo if none ran in the last half hour.

    ``off``, ``no-repo``, ``recent`` or ``spawned``. It reads one file and
    writes one. It never runs ``git`` or ``gh``.
    """
    if not enabled(cfg):
        return "off"
    from mnemo.core import agent as agent_mod

    info = agent_mod.resolve_canonical_agent(cwd or os.getcwd())
    if not info.has_git or not info.repo_root:
        return "no-repo"
    repo = str(info.repo_root)
    moment = time.time() if now is None else now
    last = (load_ledger(vault_root).get("last_run") or {}).get(repo)
    try:
        if last is not None and moment - float(last) < MIN_INTERVAL_SECONDS:
            return "recent"
    except (TypeError, ValueError):
        pass

    def change(data: Dict[str, Any]) -> None:
        runs = data.setdefault("last_run", {})
        if not isinstance(runs, dict):
            runs = data["last_run"] = {}
        runs[repo] = moment

    _update(vault_root, change)
    (spawn or _spawn_sweep)(cwd)
    return "spawned"
