"""Spawn one background child per GitHub issue, each in its own worktree.

Every constraint below was measured during the real three-issue dispatch on
2026-09-12 (#197), which produced PRs #191, #192 and #194. Each cost a failed
attempt, so each is encoded here rather than left to be rediscovered:

- **``--bg`` and ``-p/--print`` conflict.** ``--print`` never starts the
  interactive session ``claude attach`` needs, so the job would be
  unattachable. The prompt is **positional**: ``claude --bg '<task>'``.
- **``--resume <id>`` on a live session bifurcates it** rather than resuming:
  the original stays blocked and a detached copy runs. A blocked child is
  answered with ``claude attach``, or by ``SendMessage`` addressed to the name
  ``ListAgents`` shows — verified to flip a child ``blocked -> active``, and
  one-way (the child's reply goes to its own transcript). This module
  deliberately does neither: a child blocks exactly when it needs human
  judgement, and a dispatcher that answers on its own re-creates the #187
  failure below, where a prescribed answer overrode a correct refusal.
  Relaying a human's answer is useful; inventing one is not.
- **``claude agents`` requires a TTY** and refuses on a pipe. Any scripted
  reader must use ``mnemo sessions --json``.
- **``timeout`` is not on the macOS PATH.** Dispatches are never wrapped in it.

One worktree per child is mandatory, not advisory. Sharing a tree between
parallel sessions has already cost three git accidents in one turn: a branch
taken from another session's branch, ``add -A`` sweeping another session's
files, and ``--amend`` rewriting another session's commit.

**Where the issue->child mapping lives.** Nowhere new. The dispatcher chooses
the worktree path, so ``<repo>-wt-<issue>`` *is* the record, and Claude Code
already stores ``cwd`` in its own ``state.json``. A sidecar keyed by
``short_id`` would have to be written after the spawn (racing the child it
describes) and reconciled whenever a job is pruned; a path that the dispatcher
controls has neither problem. :func:`issue_for_cwd` reads it back, and
``Session.label`` uses it to label a child by its issue instead of by a title
inferred from the transcript.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

WORKTREE_SUFFIX = "-wt-"

# `<anything>-wt-<digits>`, optional trailing slash. Anchored on both ends so
# `mnemo-wt-feature` — a hand-made worktree that is not a dispatch — is not
# mistaken for one.
_WT_RE = re.compile(r"-wt-(\d+)/?$")


class DispatchError(RuntimeError):
    """A dispatch could not start, or could not finish cleanly.

    Always raised *after* any partial state has been rolled back, so a caller
    that catches it knows no worktree was left behind.
    """


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str


@dataclass(frozen=True)
class Dispatched:
    """One issue's outcome. ``error`` is set iff the dispatch failed."""

    issue: int
    worktree: Path | None = None
    short_id: str | None = None
    error: str | None = None


# --- naming: the mapping, as a convention ----------------------------------


def worktree_path(issue: int, *, repo_root: Path | str) -> Path:
    """Where issue *issue*'s child works: a **sibling** of the repo.

    A sibling, never a subdirectory: a worktree inside the repo is swept by
    ``git add -A``, walked by the extractor, and shows up in every ``grep``
    the parent runs.

    The repo's own ``-wt-<n>`` suffix is stripped first, so dispatching from
    inside a child's tree yields ``mnemo-wt-198`` rather than stacking into
    ``mnemo-wt-197-wt-198`` and nesting again on the generation after that.
    """
    root = Path(repo_root)
    base = _WT_RE.sub("", root.name)
    return root.parent / f"{base}{WORKTREE_SUFFIX}{issue}"


def branch_name(issue: int) -> str:
    return f"fix/issue-{issue}"


def issue_for_cwd(cwd: str | Path | None) -> int | None:
    """The issue number a dispatched worktree encodes, or ``None``.

    The inverse of :func:`worktree_path`. Returns ``None`` for any path this
    module did not name — an ordinary checkout, or a worktree someone created
    by hand — so a false positive cannot mislabel an unrelated session.
    """
    if not cwd:
        return None
    match = _WT_RE.search(str(cwd))
    return int(match.group(1)) if match else None


# --- the prompt: context and scope, never a solution -----------------------

_PROMPT = """Work on issue #{issue} in this repo: {title}

Read the full issue with `gh issue view {issue}` — including its comments,
which often re-scope it. The body as it stands:

---
{body}
---

You are in a git worktree of your own on branch `{branch}`. Work only here.

Scope limits:
- Do not merge or push without asking.
- Do not touch files outside what this issue needs.
- Run the full test suite before claiming the work is done.

No approach is prescribed. Decide what the issue actually calls for from the
evidence in the repo. If the issue proposes a fix that the code shows to be
wrong, say so and refuse it — a measured refusal is a better outcome than a
faithful implementation of a bad plan, and reporting that is finishing the
job, not failing it.
"""


def build_prompt(issue: int, *, title: str, body: str) -> str:
    """The child's opening prompt: the issue, a worktree, and scope limits.

    Deliberately takes no "approach" parameter. The #187 child was dispatched
    with an explicit instruction drawn from a prior measurement — "the slug is
    the cheaper signal, implement slug-keyed dedupe" — and **refused it, and
    was right**: ``evidence.verify_page`` demotes via
    ``replace(page, type="reference")`` keeping the slug, so 1386 of 1852 live
    pages are structurally cross-type collisions and the instructed change
    would have reopened #177 at vault scale. It shipped three measured
    refusals instead (PR #194).

    A well-written issue body carried more than the dispatching prompt did.
    Since a prompt that prescribes an approach can override a correct refusal,
    there is no way to pass one here.
    """
    return _PROMPT.format(
        issue=issue,
        title=title or f"issue #{issue}",
        body=(body or "").strip() or "(empty — read it with gh)",
        branch=branch_name(issue),
    )


# --- github ----------------------------------------------------------------


def fetch_issue(issue: int, *, repo_root: Path | str) -> Issue:
    """Read an issue with ``gh``. Raises :class:`DispatchError` if it cannot.

    Called *before* any git state is created, so a bad issue number costs
    nothing to refuse.
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue), "--json", "number,title,body"],
            cwd=str(repo_root), capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError) as exc:
        raise DispatchError(f"gh unavailable: {exc}") from exc

    if result.returncode != 0:
        raise DispatchError(
            f"issue #{issue} not found: {result.stderr.strip() or 'gh failed'}"
        )

    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise DispatchError(f"issue #{issue}: unreadable gh output") from exc

    return Issue(
        number=int(data.get("number") or issue),
        title=str(data.get("title") or ""),
        body=str(data.get("body") or ""),
    )


# --- worktrees -------------------------------------------------------------


def ensure_worktree(issue: int, *, repo_root: Path | str) -> Path:
    """Create issue *issue*'s worktree on its own branch, or refuse.

    Refuses rather than reuses when the path already exists: a live tree may
    hold another session's uncommitted work, and its branch may point
    somewhere entirely unrelated to this issue.

    On failure no directory is left behind — a stray tree blocks every later
    attempt at the same issue, which is worse than never having started.
    """
    target = worktree_path(issue, repo_root=repo_root)
    if target.exists():
        raise DispatchError(
            f"{target} already exists — refusing to reuse another session's tree"
        )

    branch = branch_name(issue)
    try:
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(target)],
            cwd=str(repo_root), capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError) as exc:
        raise DispatchError(f"git unavailable: {exc}") from exc

    if result.returncode != 0:
        # git refused — most often the branch already exists. Clear anything a
        # partial `worktree add` may have left so the next attempt is clean.
        remove_worktree(target, repo_root=repo_root)
        raise DispatchError(
            f"could not create worktree for #{issue}: "
            f"{result.stderr.strip() or 'git worktree add failed'}"
        )
    return target


def remove_worktree(target: Path, *, repo_root: Path | str) -> None:
    """Undo :func:`ensure_worktree`, as far as it got. Never raises.

    Used on the rollback path, where a second failure must not mask the error
    that is actually being reported.
    """
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(target)],
            cwd=str(repo_root), capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError):
        pass
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        subprocess.run(
            ["git", "worktree", "prune"], cwd=str(repo_root),
            capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError):
        pass


# --- spawn -----------------------------------------------------------------


def spawn_child(prompt: str, *, cwd: Path | str) -> str:
    """Start a detached child in *cwd*. Returns its short id.

    ``--bg`` with the prompt **positional**. Never ``-p``/``--print``: the CLI
    rejects the combination, and ``--print`` would never start the interactive
    session ``claude attach`` needs, leaving the job unattachable. Never
    wrapped in ``timeout``, which is not on the macOS PATH.
    """
    args = ["claude", "--bg", prompt]
    try:
        result = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    except (FileNotFoundError, OSError) as exc:
        raise DispatchError(f"could not spawn claude: {exc}") from exc

    if result.returncode != 0:
        raise DispatchError(
            f"claude --bg failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip().split()[-1] if result.stdout.strip() else ""


# --- orchestration ---------------------------------------------------------

Fetcher = Callable[..., Issue]


def dispatch_issue(
    issue: int, *, repo_root: Path | str, fetch: Fetcher = fetch_issue
) -> Dispatched:
    """Dispatch one issue: read it, make its tree, spawn its child.

    Ordered so that the cheapest refusal comes first — a nonexistent issue
    never reaches git — and so that anything created is removed again if a
    later step fails. Raises :class:`DispatchError` with no state left behind.
    """
    details = fetch(issue, repo_root=repo_root)  # before any git state exists
    tree = ensure_worktree(issue, repo_root=repo_root)
    try:
        short_id = spawn_child(
            build_prompt(issue, title=details.title, body=details.body), cwd=tree
        )
    except BaseException:
        # Including KeyboardInterrupt: a Ctrl-C between the two steps must not
        # leave a tree that blocks the retry.
        remove_worktree(tree, repo_root=repo_root)
        raise
    return Dispatched(issue=issue, worktree=tree, short_id=short_id)


def dispatch_all(
    issues: Sequence[int], *, repo_root: Path | str, fetch: Fetcher = fetch_issue
) -> list[Dispatched]:
    """Dispatch each issue, independently. One failure never strands the rest.

    Children are independent by construction — separate trees, separate
    branches — so a bad issue number in the middle of a list is reported and
    skipped rather than aborting the ones that would have worked.
    """
    out: list[Dispatched] = []
    for issue in issues:
        try:
            out.append(dispatch_issue(issue, repo_root=repo_root, fetch=fetch))
        except DispatchError as exc:
            out.append(Dispatched(issue=issue, error=str(exc)))
    return out
