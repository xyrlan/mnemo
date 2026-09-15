"""The last metre of a dispatch: is a child's work deliverable, and was it?

Four children of the 2026-09-13 dispatch finished their work and every one of
them stopped, unable to push (#215). The maintainer pushed and opened three
PRs by hand. The decision worth making is per-child — *is this work good?* —
and the mechanics after it are not worth repeating N times.

**What this module refuses to be.** It is not a way for a child to acquire
permission it was denied. Two of those children were stopped by their own
permission classifier and both refused to route around it, which is the
behaviour worth keeping. Everything here runs in the *parent*, under the
maintainer's own credentials, on branches the maintainer named after looking
at the diff. A command that pushes unreviewed work is the same failure as a
child that routes around its classifier, from the other side.

**Readiness comes from git, not from session state (#217).** A worktree whose
tree is clean and whose branch is ahead of the base branch is ready. That fact is
authoritative, survives the child dying, and needs nothing volunteered by
Claude Code. Contrast ``render._prs``, which read PR numbers out of
``Session.children`` — a list Claude Code writes when it happens to notice a
PR and mnemo never writes at all, which is why one child's row showed ``#212``
and another's showed a prose sentence.

**Nothing is persisted.** ``dispatch.py`` argues that a sidecar keyed by
``short_id`` would race the child it describes and need reconciling on prune;
that reasoning was about the dispatch *plan*, and the maintainer's decision is
that it extends to the outcome too. The join is derived on every read. The one
concession to cost is :class:`Cache`, which lives for a single invocation: a
``gh`` call takes ~400ms and ``--review`` makes one per row, but a file on disk
would be back to a record that can disagree with git.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from mnemo.core.dispatch import issue_for_cwd

# What a dispatched branch is measured against when the repo cannot say. Only
# the fallback: xyrlan/mnemo-desktop's default branch is `main`, and measuring
# its children against a `master` that does not exist reported every one of
# them as "no commits ahead" (#287). The real base comes from `base_branch`.
FALLBACK_BASE = "master"

# `git worktree list --porcelain` emits stanzas of `<key> <value>` lines
# separated by blank lines, the path first. Parsed rather than the plain
# `git worktree list`, whose columns are alignment-padded and whose branch
# is bracketed — both are presentation and both have changed across git
# versions. The porcelain form is the documented stable one.
_WT_LINE = re.compile(r"^worktree (.+)$")
_BRANCH_LINE = re.compile(r"^branch refs/heads/(.+)$")


def base_branch(*, repo_root: Path | str) -> str:
    """The branch dispatched work is measured against and lands onto.

    Asked of the repo, in order of cost:

    1. ``git symbolic-ref refs/remotes/origin/HEAD`` — local, no network, and
       set by every ``git clone``. Both repos this was seen in answer here
       (``origin/master`` for mnemo, ``origin/main`` for mnemo-desktop).
    2. ``gh repo view --json defaultBranchRef`` — for a repo whose remote was
       added by hand, which never gets ``origin/HEAD``. Asked only when there
       is an ``origin`` to ask about: without one ``gh`` has no repository to
       resolve and the call could only fail.
    3. :data:`FALLBACK_BASE`, when neither answers.

    Never raises. Resolved per call, never cached on disk: git is the record,
    and a copy of its answer could only disagree with it.
    """
    ref = _git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
               cwd=repo_root)
    if ref.returncode == 0:
        name = ref.stdout.strip()
        if name.startswith("origin/") and len(name) > len("origin/"):
            return name[len("origin/"):]

    if _git(["remote", "get-url", "origin"], cwd=repo_root).returncode == 0:
        try:
            result = subprocess.run(
                ["gh", "repo", "view", "--json", "defaultBranchRef",
                 "--jq", ".defaultBranchRef.name"],
                cwd=str(repo_root), capture_output=True, text=True,
            )
        except (FileNotFoundError, OSError):
            result = None
        if result is not None and result.returncode == 0:
            name = result.stdout.strip()
            # `--jq` prints a bare name. Anything that is not one — an empty
            # answer, or a stub's JSON — is not an answer.
            if name and not any(c in name for c in " \t\n[]{}\"'"):
                return name

    return FALLBACK_BASE


def _base_ref(base: str, *, worktree: Path) -> str | None:
    """The ref that carries *base* here: local first, then ``origin/``.

    Local first because the dispatch branched from it. ``origin/`` second for
    a clone that never checked the default branch out locally. ``None`` when
    neither exists — which :func:`ready` reports as such, instead of the
    "no commits ahead" a failing ``rev-list`` used to read as (#287).
    """
    for candidate, name in (
        (f"refs/heads/{base}", base),
        (f"refs/remotes/origin/{base}", f"origin/{base}"),
    ):
        if _git(["rev-parse", "--verify", "--quiet", candidate],
                cwd=worktree).returncode == 0:
            return name
    return None


class DeliveryError(RuntimeError):
    """A delivery could not be completed. Raised after the state it names."""


@dataclass(frozen=True)
class Readiness:
    """Whether one worktree's work can be pushed, and what it is.

    ``ready`` is the whole judgement: a clean tree, a branch that is not the
    base, and at least one commit ahead of it. ``reason`` is filled **only**
    when it is False, and is the sentence a refusal prints — a child that is
    not ready is refused with a reason, never pushed anyway.

    ``pr`` is the PR that already exists for this branch, looked up from git
    rather than from session state, or ``None``. It is not part of readiness:
    a branch with a PR is still clean and still ahead, and what to do about
    the existing PR is the caller's decision.
    """

    worktree: Path
    branch: str | None = None
    #: The base branch ``ahead`` and ``diffstat`` were measured against.
    base: str = FALLBACK_BASE
    target: object | None = None
    clean: bool = False
    ahead: int = 0
    diffstat: str = ""
    pr: str | None = None
    #: ``gh``'s state for ``pr`` — ``OPEN``, ``MERGED``, ``CLOSED`` — or ``""``.
    #: A branch name gets reused: ``fix/issue-158`` carried PR #192 (merged
    #: 2026-09-12) and a fresh dispatch of the same issue on 2026-09-14 was
    #: refused as "PR já existe" against that dead PR. Only an OPEN PR is one
    #: that delivering again would duplicate.
    pr_state: str = ""
    reason: str = ""

    @property
    def ready(self) -> bool:
        """True when this work can be pushed as it stands."""
        return bool(self.branch) and self.clean and self.ahead > 0

    @property
    def label(self) -> str:
        """How this tree is named in output: ``#211``, or ``c-delivery``.

        Matches ``dispatch._label`` and ``Session.label``: the ``#`` is the
        sigil that makes a bare integer read as an issue, and a slug carrying
        one would read as an issue number that does not exist.
        """
        if isinstance(self.target, int):
            return f"#{self.target}"
        if self.target:
            return str(self.target)
        return self.worktree.name


def _git(args: Sequence[str], *, cwd: Path | str) -> subprocess.CompletedProcess:
    """Run git in *cwd*, capturing both streams. Never raises on a bad exit.

    A missing git is not distinguished from a failing one here: every caller
    below treats an unusable git the same way — the fact it wanted is unknown,
    which is a refusal, not a crash.
    """
    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError) as exc:  # pragma: no cover - platform
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def _branch_of(worktree: Path) -> str | None:
    """The branch *worktree* has checked out, or ``None`` when detached."""
    result = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree)
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    # A detached HEAD reports the literal "HEAD", which is not a branch and
    # cannot be pushed by name.
    return None if not name or name == "HEAD" else name


def _is_clean(worktree: Path) -> bool:
    """True when nothing is staged, modified or untracked.

    Untracked files count. A child that wrote a new module and never added it
    would otherwise read as clean, and the push would deliver a branch missing
    the file the whole piece was about.
    """
    result = _git(["status", "--porcelain"], cwd=worktree)
    if result.returncode != 0:
        return False
    return not result.stdout.strip()


def _ahead(branch: str, *, base: str, worktree: Path) -> int:
    """How many commits *branch* has that *base* (a ref) does not.

    Counted against the local base. The dispatch just branched from it, so a
    stale local base would have to be stale by the length of the dispatch —
    and fetching here would make a read-only report touch the network.
    """
    result = _git(["rev-list", "--count", f"{base}..{branch}"], cwd=worktree)
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def _diffstat(branch: str, *, base: str, worktree: Path) -> str:
    """One line of what this branch changed, or ''.

    ``base...branch`` — three dots, the symmetric difference from the merge
    base. Two dots would count anything that landed on the base after the
    child branched as a deletion in the child's diff, so a long-running child
    would report a diff full of work it never touched.
    """
    result = _git(["diff", "--shortstat", f"{base}...{branch}"], cwd=worktree)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


class Cache:
    """Per-invocation memo for ``gh`` lookups. Never touches disk.

    ``--review`` makes one ``gh pr list`` call per row and the queue renderer
    makes one per finished session; at ~400ms each that is the whole cost of
    both commands. A process-lifetime dict removes the repeats within one
    invocation while keeping the join derived — which is the point: a record
    on disk can disagree with git, and this cannot outlive the read.
    """

    def __init__(self) -> None:
        self._prs: dict[str, str | None] = {}

    def pr_for(self, branch: str, *, repo_root: Path | str) -> str | None:
        if branch not in self._prs:
            self._prs[branch] = pr_for(branch, repo_root=repo_root)
        return self._prs[branch]


@dataclass(frozen=True)
class PR:
    """The PR opened from a branch: where it is, and whether it landed.

    ``state`` is ``gh``'s own vocabulary — ``OPEN``, ``MERGED``, ``CLOSED`` —
    passed through rather than translated, so a reader can match it against
    what ``gh pr view`` shows them.
    """

    url: str
    state: str


def pr_info(branch: str, *, repo_root: Path | str) -> PR | None:
    """The PR opened from *branch* with its state, or ``None``.

    ``--state all``, not the default ``open``. A merged branch whose PR is
    closed must still report that PR: ``deliver`` uses this to decide whether
    delivering again would open a duplicate, and "no open PR" is not "no PR".
    Landing (#236) needs the state itself: a ``MERGED`` piece is skipped,
    an ``OPEN`` one is rehearsed, a ``CLOSED`` one is refused.

    Returns ``None`` for every failure — no ``gh``, no network, not
    authenticated, no such branch. This is a *join*, not a check: a missing
    answer degrades the row it decorates and must never fail the command that
    asked. The push path does not rely on it for safety.
    """
    if not branch:
        return None
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "all",
             "--json", "number,url,state", "--limit", "1"],
            cwd=str(repo_root), capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None

    import json

    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError:
        return None
    if not isinstance(rows, list) or not rows:
        return None
    first = rows[0]
    if not isinstance(first, dict):
        return None
    url = first.get("url")
    if not (isinstance(url, str) and url):
        number = first.get("number")
        if not number:
            return None
        url = f"#{number}"
    state = first.get("state")
    return PR(url=url, state=str(state) if state else "")


def pr_for(branch: str, *, repo_root: Path | str) -> str | None:
    """The URL of the PR opened from *branch*, or ``None``.

    The URL half of :func:`pr_info`, kept for the callers that only decorate a
    row with it. One ``gh`` shape, two readers, so they cannot drift.
    """
    info = pr_info(branch, repo_root=repo_root)
    return info.url if info else None


def pr_lookup(*, repo_root: Path | str):
    """A ``Session -> PR`` callable for ``render.render_queue``.

    The authoritative half of #217, packaged so the queue can take it in one
    line. The queue renderer is pure by contract and must not call ``gh``
    itself; this closure carries the ``gh`` call and a :class:`Cache`, so a
    ``--watch`` redraw asks once per branch rather than once per tick.

    A session whose ``cwd`` this dispatcher did not name yields ``None`` and
    the row falls back to what the child volunteered — unchanged behaviour for
    every session that is not a dispatch child.
    """
    cache = Cache()

    def _lookup(session) -> str | None:
        from mnemo.core.dispatch import branch_name

        target = issue_for_cwd(getattr(session, "cwd", None))
        if target is None:
            return None
        branch = _branch_of(Path(session.cwd))
        if branch is None:
            # The tree is gone — pruned, or the job outlived it. The branch is
            # still derivable for an issue child, whose branch name needs no
            # feature; a piece slug does need one, and nothing here has it.
            if not isinstance(target, int):
                return None
            branch = branch_name(target)
        return cache.pr_for(branch, repo_root=repo_root)

    return _lookup


def ready(
    worktree: Path | str, *, repo_root: Path | str, base: str | None = None,
) -> Readiness:
    """Whether *worktree*'s work can be pushed, and what it is.

    Every fact comes from git run inside *worktree*. Nothing is asked of the
    session that made it, so this answers the same way whether the child is
    still running, blocked, or dead — which is the property #217 wanted: the
    association is re-derived, and re-derivable is the point.

    The PR lookup is the one call that leaves git, and its failure is not a
    failure of readiness (see :func:`pr_for`).

    *base* is the branch to measure against; ``None`` resolves it with
    :func:`base_branch`. A caller checking many trees resolves it once.
    """
    tree = Path(worktree)
    target = issue_for_cwd(tree)
    if base is None:
        base = base_branch(repo_root=repo_root)

    if not tree.exists():
        return Readiness(
            worktree=tree, target=target, base=base,
            reason="worktree is gone — nothing left to deliver",
        )

    branch = _branch_of(tree)
    if branch is None:
        return Readiness(
            worktree=tree, target=target, base=base,
            reason="detached HEAD — no branch to push",
        )
    if branch == base:
        # Not a dispatch tree, or one whose branch was already swapped back.
        # Pushing the base from here is exactly the accident worth refusing.
        return Readiness(
            worktree=tree, branch=branch, target=target, base=base,
            reason=f"on {base} — a dispatch child works on its own branch",
        )

    measured = _base_ref(base, worktree=tree)
    if measured is None:
        # Not "nothing to deliver": nothing was measured. Saying so names the
        # branch that is missing, which is the fact a maintainer can act on.
        return Readiness(
            worktree=tree, branch=branch, target=target, base=base,
            clean=_is_clean(tree),
            reason=f"base branch {base} not found locally or on origin — "
                   f"cannot count commits ahead of it",
        )

    clean = _is_clean(tree)
    ahead = _ahead(branch, base=measured, worktree=tree)
    stat = _diffstat(branch, base=measured, worktree=tree) if ahead else ""
    info = pr_info(branch, repo_root=repo_root)
    pr = info.url if info else None
    pr_state = info.state if info else ""

    reason = ""
    if not clean and ahead == 0:
        reason = "uncommitted work and nothing committed yet"
    elif not clean:
        # The commits are real, but the tree holds more. Pushing now delivers
        # a partial piece and the rest looks like a follow-up that never came.
        reason = "uncommitted changes — commit them or discard them first"
    elif ahead == 0:
        reason = f"no commits ahead of {base} — nothing to deliver"

    return Readiness(
        worktree=tree, branch=branch, target=target, base=base, clean=clean,
        ahead=ahead, diffstat=stat, pr=pr, pr_state=pr_state, reason=reason,
    )


def dispatch_worktrees(*, repo_root: Path | str) -> list[Path]:
    """Every dispatch worktree git knows about, in the order git lists them.

    Read from ``git worktree list``, not from the session queue. Both know the
    trees, and they know them differently: the queue knows a tree only while a
    session that ran in it is still on disk under ``~/.claude/jobs``, while git
    knows the tree itself. A child that died, or whose job was pruned, still
    has work worth delivering — and that work is exactly what this command
    exists to rescue. Git outliving the session is the same reason readiness
    is read from git.

    Filtered by :func:`issue_for_cwd`, so a worktree someone created by hand
    is not offered for delivery under a label this dispatcher never chose.
    """
    result = _git(["worktree", "list", "--porcelain"], cwd=repo_root)
    if result.returncode != 0:
        return []

    out: list[Path] = []
    for line in result.stdout.splitlines():
        match = _WT_LINE.match(line)
        if match and issue_for_cwd(match.group(1)) is not None:
            out.append(Path(match.group(1)))
    return out


def find_worktree(short_id: str, *, repo_root: Path | str) -> Path | None:
    """The worktree a maintainer named, by session short id or by target.

    Three spellings are accepted because three are in front of the maintainer
    when they decide. ``mnemo sessions`` prints the **short id** in column one
    and the target in the label; ``mnemo deliver --review`` prints the target;
    and the worktree path itself is what ``git worktree list`` shows. Requiring
    one of them would mean reading a row and then translating it by hand, which
    is the context switch this whole piece exists to remove.

    The short id resolves through the queue, the only thing that maps a session
    to its ``cwd``. That is a convenience, not the authority: if the job is
    gone the target still resolves, because git still has the tree.
    """
    named = str(short_id)

    # A target — `211`, `#211`, `c-delivery`, `delivery`. Matched first
    # because it is what --review prints, and because it costs no I/O beyond
    # the worktree list this needs anyway.
    wanted = named.lstrip("#")
    for tree in dispatch_worktrees(repo_root=repo_root):
        target = issue_for_cwd(tree)
        if str(target) == wanted or (
            isinstance(target, str) and target == f"c-{wanted}"
        ):
            return tree

    # A session short id, or a unique prefix of one, as `mnemo session` takes.
    try:
        from mnemo.core.sessions.jobs import read_sessions

        matches = [
            s for s in read_sessions()
            if s.short_id == named or s.short_id.startswith(named)
        ]
    except Exception:
        return None
    # A prefix matching two sessions names neither of them. Refusing is the
    # only safe answer: picking one would push a branch the maintainer did
    # not look at, which is the invariant this module is built around.
    if len(matches) != 1:
        return None
    cwd = matches[0].cwd
    return Path(cwd) if cwd else None


def push(branch: str, *, worktree: Path | str) -> None:
    """Push *branch* to ``origin``, setting upstream. Raises on failure.

    ``--set-upstream`` so the branch the maintainer just delivered behaves
    like one they created themselves afterwards. Never ``--force``: a delivery
    that would have to overwrite the remote is a conflict the maintainer has
    to see, and the refusal is the report.
    """
    result = _git(
        ["push", "--set-upstream", "origin", branch], cwd=worktree,
    )
    if result.returncode != 0:
        raise DeliveryError(
            result.stderr.strip() or result.stdout.strip() or "git push failed"
        )


# GitHub's closing keywords, as it documents them. Matched to decide whether
# a body already closes the issue, so a child that wrote its own trailer is
# not corrected into a duplicate one. `#<n>` alone is deliberately not here:
# a bare reference is exactly what did *not* close #222.
_CLOSES_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s+#(\d+)\b",
    re.IGNORECASE,
)


def _closes_already(body: str, issue: int) -> bool:
    """True when *body* already carries a closing keyword for *issue*."""
    return any(int(n) == issue for n in _CLOSES_RE.findall(body))


def _append_closing_trailer(url: str, *, issue: int, worktree: Path | str) -> None:
    """Append ``Closes #<issue>`` to the body ``--fill`` wrote. Never raises.

    Two calls rather than one because ``--body`` passed alongside ``--fill``
    *overwrites* the filled body rather than extending it — ``gh`` documents
    that precedence — so the child's prose can only be preserved by reading it
    back and editing it.

    Addressed by *url* and not by the branch: ``gh pr edit`` with no argument
    resolves the PR from the current branch, which is the right one only
    because the worktree happens to be on it.

    Failure is swallowed on purpose. The PR exists and the branch is pushed;
    raising would send the maintainer to retry a ``deliver`` that refuses as a
    duplicate, when the only thing missing is one line they can add in the UI.
    """
    try:
        got = subprocess.run(
            ["gh", "pr", "view", url, "--json", "body", "--jq", ".body"],
            cwd=str(worktree), capture_output=True, text=True,
        )
        if got.returncode != 0:
            return
        body = got.stdout.strip("\n")
        if _closes_already(body, issue):
            return
        subprocess.run(
            ["gh", "pr", "edit", url, "--body", f"{body}\n\nCloses #{issue}"],
            cwd=str(worktree), capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError):
        return


def open_pr(
    branch: str,
    *,
    worktree: Path | str,
    title: str,
    target: object | None = None,
) -> str:
    """Open a PR for *branch* with ``gh``. Returns its URL. Raises on failure.

    ``--fill`` rather than a generated body: the commits are the description,
    they were written by the child that did the work, and this process knows
    strictly less about the change than they do. A body invented here would be
    a summary of a diff nobody in this process read — the same failure as
    ``_prs`` trusting whatever a child volunteered, with the direction
    reversed. ``--title`` is passed because ``--fill`` alone titles a
    multi-commit PR after its first commit, which on a dispatch piece is
    usually its least representative one.

    That reasoning is unchanged by #224, and the fix respects it: the body
    stays the child's, and the *one* fact this process owns and the body does
    not carry — the issue number, recovered from the worktree path by
    :func:`issue_for_cwd` — is appended as a ``Closes #<n>`` trailer.

    Merging PR #223 did not close #222 because the child's subject read
    ``fix(sessions): ... (#222)``, which GitHub treats as a reference and not
    as a closing keyword. Asking the child to write the trailer was rejected:
    it makes the dispatcher trust a child to report something the dispatcher
    already knows, the fragility #217 named — and #223 is the demonstration,
    a child that named its issue and still did not close it.

    *target* is what :func:`issue_for_cwd` returned. Only an ``int`` gets a
    trailer; a ``c-<slug>`` contract piece has no issue to close.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "create", "--head", branch, "--title", title, "--fill"],
            cwd=str(worktree), capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError) as exc:
        raise DeliveryError(f"gh unavailable: {exc}") from exc
    if result.returncode != 0:
        raise DeliveryError(
            result.stderr.strip() or result.stdout.strip() or "gh pr create failed"
        )

    url = ""
    for token in result.stdout.split():
        if token.startswith("http"):
            url = token
            break
    # Shape-matched, like `dispatch._short_id_from`, and empty rather than
    # guessed when nothing matches: the PR was created either way, and a
    # blank URL reads as missing where a wrong one reads as actionable.

    # `isinstance(True, int)` is True, and `target` crosses a dataclass field
    # typed `object`; a bool here would name issue #1.
    if url and isinstance(target, int) and not isinstance(target, bool):
        _append_closing_trailer(url, issue=target, worktree=worktree)
    return url


# --- after the PR: stop the finished child (#311) ---------------------------


def sessions_in(worktree: Path | str) -> list:
    """Every background session whose ``cwd`` is *worktree*. ``[]`` on any error.

    Joined on ``cwd``, the one key Claude Code and dispatch both write
    (``jobs-state-json``). More than one is possible: a branch name — and so
    a tree path — gets reused (``fix/issue-158``), and the jobs of the earlier
    life can still be on disk.
    """
    try:
        from mnemo.core.sessions.jobs import normalize_cwd, read_sessions

        wanted = normalize_cwd(str(worktree))
        return [s for s in read_sessions() if normalize_cwd(s.cwd) == wanted]
    except Exception:
        return []


def stop_session(short_id: str) -> str | None:
    """``claude stop <short_id>``. ``None`` when it exited 0, else why not.

    Never raises: the PR is already open when this runs, and a stop that
    failed must not read as a delivery that failed. On a ``done`` child the
    stop ends the process and leaves ``state=done`` (``stop-rm-noninteractive``,
    ``daemon-spare-pool``), so the queue row does not move — and only a
    stopped child fires ``SessionEnd`` (#247), which is where its briefing is
    written.
    """
    try:
        result = subprocess.run(
            ["claude", "stop", short_id],
            capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr.strip() or result.stdout.strip()
                or f"exit {result.returncode}")
    return None
