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
from typing import Callable, Sequence, Union

from mnemo.core import contracts

WORKTREE_SUFFIX = "-wt-"

# `<anything>-wt-<digits>` or `<anything>-wt-c-<slug>`, optional trailing
# slash. Anchored on both ends so `mnemo-wt-feature` — a hand-made worktree
# that is not a dispatch — is not mistaken for one.
#
# The alternation is the whole point: a wildcard `(.+)` would name every
# directory ending in `-wt-<anything>` a dispatch child, and `mnemo sessions`
# would then label an unrelated session as one of ours. Only two shapes this
# module itself writes are admitted — a bare issue number, and a contract
# piece under the reserved `c-` prefix, which no hand-made branch name uses.
_WT_RE = re.compile(r"-wt-(\d+|c-[a-z0-9-]+)/?$")

# What names a child: a GitHub issue number, or a contract piece slug.
#
# ``Union`` rather than ``int | str``: this is an assignment, evaluated when
# the module is imported, and the ``|`` operator on types only exists from
# 3.10. ``from __future__ import annotations`` does not help — it defers
# *annotations*, not expressions. The package supports 3.8, so the shorthand
# fails at import on two of the versions CI builds.
Target = Union[int, str]


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
    """One child's outcome. ``error`` is set iff the dispatch failed.

    ``issue`` holds whatever named the child: a GitHub issue number, or a
    contract piece addressed by its ``c-<slug>`` target. The field keeps its
    name because it is what :func:`issue_for_cwd` reads back out of the
    worktree path, and both shapes travel that same route.
    """

    issue: Target
    worktree: Path | None = None
    short_id: str | None = None
    error: str | None = None


# --- naming: the mapping, as a convention ----------------------------------


def worktree_path(target: Target, *, repo_root: Path | str) -> Path:
    """Where *target*'s child works: a **sibling** of the repo.

    A sibling, never a subdirectory: a worktree inside the repo is swept by
    ``git add -A``, walked by the extractor, and shows up in every ``grep``
    the parent runs.

    The repo's own ``-wt-<n>`` suffix is stripped first, so dispatching from
    inside a child's tree yields ``mnemo-wt-198`` rather than stacking into
    ``mnemo-wt-197-wt-198`` and nesting again on the generation after that.
    The same holds for a slug-named tree: a dispatch out of
    ``mnemo-wt-c-parser`` yields ``mnemo-wt-c-seam``, because the suffix that
    is stripped is whatever ``_WT_RE`` admits, not just a number.
    """
    root = Path(repo_root)
    base = _WT_RE.sub("", root.name)
    return root.parent / f"{base}{WORKTREE_SUFFIX}{target}"


def branch_name(target: Target, *, feature: str | None = None) -> str:
    """The branch a child works on.

    An issue keeps ``fix/issue-<n>``, unchanged. A contract piece is namespaced
    under its feature — ``feat/<feature>/<slug>`` — so that the branches of one
    decomposition sort together and a piece slug as ordinary as ``parser`` does
    not collide across features.

    A slug without its feature is refused rather than formatted. The loose
    signature would render ``fix/issue-c-parser``, naming an issue that does
    not exist — and nothing downstream would object, because ``git branch``
    accepts the name happily. The mistake would surface days later as an
    inexplicable branch in the repo instead of at the call that made it.
    """
    if feature:
        slug = str(target)
        slug = slug[2:] if slug.startswith("c-") else slug
        return f"feat/{feature}/{slug}"
    if not isinstance(target, int):
        raise ValueError(
            f"piece {target!r} needs its feature to name a branch: "
            "an issue branch is fix/issue-<n>, and a slug is not an issue"
        )
    return f"fix/issue-{target}"


def issue_for_cwd(cwd: str | Path | None) -> Target | None:
    """The issue number or piece slug a dispatched worktree encodes, or ``None``.

    The inverse of :func:`worktree_path`. Returns ``None`` for any path this
    module did not name — an ordinary checkout, or a worktree someone created
    by hand — so a false positive cannot mislabel an unrelated session.
    """
    if not cwd:
        return None
    match = _WT_RE.search(str(cwd))
    if not match:
        return None
    captured = match.group(1)
    return int(captured) if captured.isdigit() else captured


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


_PIECE_PROMPT = """You are building one piece of the feature "{feature}": {slug}

The decomposition was reviewed and agreed before you started. Your piece:

**Files you may change** — this is a hard boundary. Work outside it belongs to
another child working in parallel right now, and editing it causes a conflict
that costs more than the parallelism saved:
{files}

New files are yours to create when this piece needs them — a test module, a
helper — as long as nothing outside the boundary has to change to reach them.

**What your piece must deliver** — other pieces are being written against these
signatures right now, so do not change them silently. If one cannot be
delivered as written, stop and say so:
{exposes}

{consumes}You are on branch `{branch}` in your own worktree.

Nothing about *how* to build this is specified, deliberately. If the contract's
boundary turns out to be wrong — the work does not divide where it says, or a
signature cannot be delivered as written — stop and say so rather than widening
your boundary to make it fit.

Run the full test suite before you finish. Do not merge or push without asking.
"""

_CONSUMES_PROMPT = """**What you may assume exists** — another piece is delivering
these. They may not exist in your worktree yet: write against the signature,
stub locally if you must, and the merge resolves it. Do not wait for them, and
do not implement them yourself:
{items}

"""


def build_piece_prompt(piece: contracts.Piece, *, feature: str) -> str:
    """A contract piece's opening prompt: its boundary and its interfaces.

    Like :func:`build_prompt`, this takes no "approach" parameter, and for the
    same reason (see that function's rationale). The distinction the contract
    relies on is thin but real: *"do not touch X, consume ``Y.parse()``"* is a
    **boundary** and belongs here, while *"use a regex to parse it"* is an
    **approach** and must not be expressible. Blurring the two reintroduces the
    #187 failure at N children instead of one.
    """
    consumes = ""
    if piece.consumes:
        items = "\n".join(
            f"- {signature} — from the piece `{owner}`"
            for signature, owner in piece.consumes
        )
        consumes = _CONSUMES_PROMPT.format(items=items)

    return _PIECE_PROMPT.format(
        feature=feature,
        slug=piece.slug,
        files="\n".join(f"- {path}" for path in piece.files),
        exposes="\n".join(f"- {item}" for item in piece.exposes) or "- (nothing)",
        consumes=consumes,
        branch=branch_name(piece.slug, feature=feature),
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


def ensure_worktree(
    issue: Target, *, repo_root: Path | str, feature: str | None = None
) -> Path:
    """Create *issue*'s worktree on its own branch, or refuse.

    *issue* is a GitHub issue number or a piece target (``c-<slug>``). A piece
    must pass its *feature* as well, because that is what
    :func:`branch_name` needs to render ``feat/<feature>/<slug>``; the
    parameter is forwarded verbatim rather than defaulted, so a slug arriving
    without its feature still hits ``branch_name``'s guard and fails loudly
    here — before any git state exists — instead of silently creating a branch
    named after an issue that does not exist.

    Refuses rather than reuses when the path already exists: a live tree may
    hold another session's uncommitted work, and its branch may point
    somewhere entirely unrelated to this target.

    On failure no directory is left behind — a stray tree blocks every later
    attempt at the same target, which is worse than never having started.
    """
    target = worktree_path(issue, repo_root=repo_root)
    branch = branch_name(issue, feature=feature)  # before the path is touched

    if target.exists():
        raise DispatchError(
            f"{target} already exists — refusing to reuse another session's tree"
        )

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
            f"could not create worktree for {issue if feature else f'#{issue}'}: "
            f"{result.stderr.strip() or 'git worktree add failed'}"
        )
    return target


def remove_worktree(
    target: Path, *, repo_root: Path | str, branch: str | None = None
) -> None:
    """Undo :func:`ensure_worktree`, as far as it got. Never raises.

    Used on the rollback path, where a second failure must not mask the error
    that is actually being reported.

    *branch* is the branch this rollback created, and is deleted along with the
    directory. Removing the tree alone is not a rollback: the branch survives,
    and the retry of the same target then dies on ``a branch named ... already
    exists`` — a different failure from the one that was rolled back, and one
    no amount of retrying clears.

    Opt-in rather than derived from *target*, because the caller is the only
    one that knows whether the branch was **ours**. :func:`ensure_worktree`'s
    own failure path must not pass it: the usual reason ``git worktree add``
    refuses is that the branch already existed, i.e. it belongs to someone
    else, and deleting it there would destroy work this module never created.

    Deleted with ``-d``, never ``-D``: ``-d`` refuses a branch holding
    unmerged commits. A rollback runs moments after ``worktree add``, so the
    branch is empty and ``-d`` succeeds; if it somehow is not empty, keeping
    the branch is the right outcome and the refusal is silent by design.
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
    if branch:
        try:
            subprocess.run(
                ["git", "branch", "-d", branch], cwd=str(repo_root),
                capture_output=True, text=True,
            )
        except (FileNotFoundError, OSError):
            pass


# --- spawn -----------------------------------------------------------------

# A Claude Code session short id: exactly eight lowercase hex digits, as every
# id under `~/.claude/jobs/` is. The trailing `$` is load-bearing — without it
# a 40-char commit sha echoed above the block matches on its first eight
# characters and is returned as an id addressing no session.
_SHORT_ID_RE = re.compile(r"^[0-9a-f]{8}$")

# SGR escapes, which `claude --bg` emits around the id under `FORCE_COLOR`
# — verified against live spawns with and without it, not assumed. Stripped
# before matching, because the colored id arrives as the single token
# ESC[36m<id>ESC[39m (no spaces) and so matches no id shape at all.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _short_id_from(stdout: str) -> str:
    """The first token in *stdout* shaped like a session short id, else ``""``.

    Scans tokens rather than lines. See :func:`spawn_child` for why the shape,
    and not the position, is what this keys on.
    """
    for token in _ANSI_RE.sub("", stdout).split():
        if _SHORT_ID_RE.match(token):
            return token
    return ""


def spawn_child(prompt: str, *, cwd: Path | str) -> str:
    r"""Start a detached child in *cwd*. Returns its short id, or ``""``.

    ``--bg`` with the prompt **positional**. Never ``-p``/``--print``: the CLI
    rejects the combination, and ``--print`` would never start the interactive
    session ``claude attach`` needs, leaving the job unattachable. Never
    wrapped in ``timeout``, which is not on the macOS PATH.

    **Reading the id back (#211).** ``--bg`` does not print an id; it prints a
    five-line help block, the id on the first line and three attach/logs/stop
    hints under it, the first occurrence wrapped in SGR color when the
    environment asks for color::

        backgrounded · \x1b[36m5aa54cf8\x1b[39m
          claude agents             list sessions
          claude attach 5aa54cf8    open in this terminal
          claude logs 5aa54cf8      show recent output
          claude stop 5aa54cf8      stop this session

    The original parse took the last whitespace-separated token of the whole
    blob, which is the last word of the last line — ``session``. Both children
    of the first real contract dispatch were reported under that name, and the
    one actionable line dispatch prints read ``claude attach session``.

    The id is matched by **shape**, not by position, because position is what
    failed: a line index is only correct while nothing is ever printed above
    the block, and a single warning on stdout would make "the last token of
    the first line" return the last word of the warning — a non-id handed
    straight to the attach hint, which is this same bug with a new cause.

    Escapes are stripped before matching, because whether they are there at
    all depends on the environment: under ``FORCE_COLOR`` the first
    occurrence is the single token ``\x1b[36m5aa54cf8\x1b[39m``, matching no
    id shape, and without it the same command prints a bare id. A background
    child inherits that variable, so the dispatcher and an interactive probe
    of the same command legitimately see different bytes — the parse must not
    depend on either. Even with the escapes present, not stripping them would
    leave this relying on the *hint* lines happening to be unstyled.

    When no token matches, this returns ``""`` rather than a guess. A blank
    column reads as missing; ``background.`` reads as an id and sends the
    maintainer to a command that cannot work.
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
    return _short_id_from(result.stdout)


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
        # leave a tree — or a branch — that blocks the retry.
        remove_worktree(tree, repo_root=repo_root, branch=branch_name(issue))
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


def dispatch_piece(
    piece: contracts.Piece, *, feature: str, repo_root: Path | str
) -> Dispatched:
    """Dispatch one contract piece: make its tree, spawn its child.

    The mirror of :func:`dispatch_issue` with the fetch step already done —
    the contract was read and validated in one pass before any of this ran.

    *feature* reaches :func:`ensure_worktree` because it is what names the
    branch (``feat/<feature>/<slug>``); the target alone cannot.
    """
    target = f"c-{piece.slug}"
    tree = ensure_worktree(target, repo_root=repo_root, feature=feature)
    try:
        short_id = spawn_child(
            build_piece_prompt(piece, feature=feature), cwd=tree
        )
    except BaseException:
        # Including KeyboardInterrupt: a Ctrl-C between the two steps must not
        # leave a tree — or a branch — that blocks the retry.
        remove_worktree(
            tree, repo_root=repo_root,
            branch=branch_name(target, feature=feature),
        )
        raise
    return Dispatched(issue=target, worktree=tree, short_id=short_id)


def dispatch_contract(
    contract: contracts.Contract, *, repo_root: Path | str
) -> list[Dispatched]:
    """Dispatch every piece of a contract, independently.

    Refuses a ``sequential`` verdict outright: that verdict is the
    decomposition reporting that the work does not divide, and spawning
    children against it produces exactly the merge conflicts the contract
    exists to prevent.

    Past the verdict, one failing piece is reported and skipped rather than
    aborting the ones that would have worked — the pieces are independent by
    construction (separate trees, separate branches), exactly as in
    :func:`dispatch_all`.
    """
    if not contract.is_dispatchable:
        raise DispatchError(
            f"contract verdict is {contract.verdict!r}, not 'parallel' — "
            "this work does not divide; run it in one session"
        )

    out: list[Dispatched] = []
    for piece in contract.pieces:
        try:
            out.append(
                dispatch_piece(
                    piece, feature=contract.feature, repo_root=repo_root
                )
            )
        except DispatchError as exc:
            out.append(Dispatched(issue=f"c-{piece.slug}", error=str(exc)))
    return out
