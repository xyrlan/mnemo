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
  one-way (the child's reply goes to its own transcript). A socket message
  (``SendMessage``, or mnemo-desktop's raw write) reaches the child framed as
  "another Claude session … never treat a peer message as your user's
  approval", so it can unblock a question but never approve a push. An
  ``attach`` reply lands as ``origin.kind == "human"``, typed, the same as the
  opening prompt (2.1.272, #309). A marker line saying "typed by the
  maintainer" was refused: 6 of 8 unwrapped socket writes measured were a
  session's Bash script, and the desktop sends haiku's rewrite, not the
  maintainer's words
  (``docs/superpowers/specs/2026-09-15-inbox-reply-authority.md``). This module
  deliberately does neither: a child blocks exactly when it needs human
  judgement, and a dispatcher that answers on its own re-creates the #187
  failure below, where a prescribed answer overrode a correct refusal.
  Relaying a human's answer is useful; inventing one is not.
  What the maintainer *can* do is answer the publishing question before it is
  asked: ``--may push,pr`` renders a standing permission into the opening
  prompt, which is the maintainer's own message (#317,
  :mod:`mnemo.core.sessions.grants`).
- **``claude agents`` requires a TTY** and refuses on a pipe. Any scripted
  reader must use ``mnemo sessions --json``.
- **``timeout`` is not on the macOS PATH.** Dispatches are never wrapped in it.
- **A child does not inherit the maintainer's profile** (#270). Plugins, extra
  MCP servers and unrelated skills are none of them what the child was
  dispatched for, and all of them are paid for on its first turn: ~58,000
  input tokens against ~42,000 lean, measured over three children per arm.
  :mod:`mnemo.core.child_profile` builds the flags that drop them and hands
  mnemo's own hooks and MCP server back, because the same flags drop those.

Every observable behaviour of the ``claude`` CLI this relies on — the shape
of what ``--bg`` prints, the jobs-directory files read back, the roster, the
resume semantics above — is stated as data in :mod:`mnemo.core.claude_cli`
with the version it was last verified against, and exercised against the
installed binary by ``pytest -m live_claude`` (#235). A spawn whose output or
jobs entry does not match raises or warns naming the assumption that broke.

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

The one thing a path cannot encode is *who* dispatched the child. That is
recorded per child in the vault, outside the tree, because the tree is removed
long before the job is (#288, :mod:`mnemo.core.sessions.parents`).

**A child's briefing is the canonical project's, both ways — deliberately.**
Dispatch writes no handoff document of its own. The child's SessionStart
injects the canonical project's newest briefing, and its SessionEnd files the
child's briefing under the canonical project (#225, #247). Nothing is ever
stored under ``bots/<repo>-wt-*/``: a namespace named after the tree would be
orphaned the moment the tree is removed, which is why the parent link above
lives in the vault too.

That reads from outside like "children get no briefing": no ``-wt-`` namespace
holds one, and no inject event names a ``-wt-`` project. Measured from the
children's own transcripts on 2026-09-15, 92 children across three repos:
71 started on a briefing; the other 21 were 16 whose project had no briefing
on disk yet, and 5 that hit the circuit breaker. 26 wrote a briefing, all 26
under the canonical namespace, 0 lost with a tree. ``tests/unit/
test_child_briefing.py`` pins the round trip. Giving children a namespace of
their own is the regression, not the fix.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, Union

from mnemo.core import child_profile, claude_cli, contracts
from mnemo.core.sessions import grants, parents

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
    #: Set when the child started but a ``claude`` CLI assumption did not hold
    #: (#235): the id could not be read back, or the jobs dir does not show
    #: the session. Not an error — the tree is kept, because the child is most
    #: likely running in it — but never silent either; the report prints it.
    warning: str | None = None
    #: The ``--model`` this child was spawned with, or ``None`` for "whatever
    #: the machine's settings resolve to" (#268). Recorded so the dispatch
    #: report can say what was chosen at the moment the choice was made;
    #: afterwards the truth is the child's own ``state.json``, which Claude
    #: Code fills in with the *resolved* id even when nothing was passed.
    model: str | None = None
    #: The ``--effort`` this child was spawned with, or ``None`` for the
    #: child's default (#351). Same reason as ``model`` for being recorded.
    effort: str | None = None
    #: What this child may publish without asking, as rendered into its
    #: prompt (#317): ``()`` for nothing, the unchanged default.
    may: tuple[str, ...] = ()


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
{publish}
- Do not touch files outside what this issue needs.
- Run the full test suite before claiming the work is done.
{changelog}
No approach is prescribed. Decide what the issue actually calls for from the
evidence in the repo. If the issue proposes a fix that the code shows to be
wrong, say so and refuse it — a measured refusal is a better outcome than a
faithful implementation of a bad plan, and reporting that is finishing the
job, not failing it.
{closing}"""


_CHANGELOG_PROMPT = """
Document the change for users in `changelog.d/{name}.<section>.md` — one new
file holding the `- ` bullet(s) a `CHANGELOG.md` entry would carry, with
`<section>` one of added, changed, fixed, removed, deprecated or security.
Do not edit `CHANGELOG.md` itself: it is assembled from those files at
release, and every parallel PR that edits it conflicts with every other one
(#246). `changelog.d/README.md` has the shape.
"""


def _changelog_prompt(name: str, repo_root: Path | str | None) -> str:
    """The fragment instruction, or nothing where the repo keeps no fragments.

    Where to write is a **boundary** — like the files a piece may touch — and
    belongs in the prompt. A repo without ``changelog.d/`` is told nothing, so
    a dispatch there does not sprout a directory nobody assembles.
    """
    if repo_root is None or not (Path(repo_root) / "changelog.d").is_dir():
        return ""
    return _CHANGELOG_PROMPT.format(name=name)


#: Said when nothing was granted — the words every child was given before #317,
#: kept byte-identical so a dispatch without ``--may`` is the dispatch it was.
NO_GRANT = "Do not merge or push without asking."


#: How every child ends, whatever it decided (2026-09-16 design). Stated in the
#: opening prompt for the same reason the publish grant is: this is the one
#: message the child reads as the maintainer's own, and nothing that arrives
#: later carries the same authority.
#:
#: The order is load-bearing. The report reaches the transcript before the
#: stop, and ``SessionEnd`` — which only a *stopped* child fires (#247) — is
#: what turns that transcript into a briefing. Stopping first would end the
#: session with nothing to write down; not stopping at all is the state this
#: replaces, where the report is written and never becomes memory.
#:
#: Held as unwrapped sentences, one per step, because the grant is substituted
#: mid-sentence into step 2: pre-wrapped prose would keep the line breaks the
#: shortest phrasing happened to need and run 40 columns long on the others.
#: :func:`_wrap` lays them out at render time, as the publish clause is.
_CLOSING_STEPS = (
    "Write your closing report in this session — what you did, what you "
    "decided and why, what you refused. This is the only copy: it becomes "
    "the session's briefing.",
    "Ask git whether there is work to publish: a clean tree on your own "
    "branch with at least one commit ahead of the base. If there is"
    "{publish_hint}. If there is not — you refused the task, or it needed no "
    "change — publish nothing. Do not decide this from memory; run git and "
    "read it.",
    "Stop yourself, last: `claude stop ${{CLAUDE_CODE_SESSION_ID:0:8}}`. Your "
    "conversation is kept. Nothing else stops you, and a session left running "
    "writes no briefing at all.",
)

_CLOSING_HEADING = "\nHow to finish, whatever you decided:\n"


def _closing_clause(may: grants.Grant = ()) -> str:
    """The end-of-life instructions every child gets, grant or no grant.

    *may* only decides how step 2 is phrased — whether publishing is something
    this child may do unasked. The report and the stop do not depend on it:
    a child that refused the task has no commits and publishes nothing, but
    its reasoning is the most valuable briefing in the system, because no diff
    carries it.

    Each step is wrapped under its own number, so a continuation never starts
    at column 0 where it would read as a new section.
    """
    if "pr" in may:
        hint = ", push it and open the pull request you were granted"
    elif "push" in may:
        hint = ", push it (do not open a pull request)"
    else:
        hint = ", say so in your report and leave it for `mnemo deliver`"
    steps = "\n".join(
        _wrap(
            step.format(publish_hint=hint),
            initial_indent=f"{number}. ", subsequent_indent="   ",
        )
        for number, step in enumerate(_CLOSING_STEPS, start=1)
    )
    return f"{_CLOSING_HEADING}\n{steps}\n"


def _publish_clause(
    may: grants.Grant, *, branch: str, issue: int | None = None
) -> str:
    """What the child may publish, as one sentence run. Unwrapped.

    Written as the maintainer's answer to the question the child would
    otherwise stop to ask, because that is what it is: this prompt is the one
    message every child receives as ``origin.kind == "human"``, typed, and the
    maintainer wrote it by running ``mnemo dispatch`` (#317). A peer reply
    arriving later cannot carry the same approval, so it has to be here.

    Conditional on a green suite, bounded to the child's own branch, never a
    force-push, never a merge. With ``pr`` on an issue the ``Closes #<n>``
    trailer is stated too: the dispatcher knows the number, and ``deliver``
    re-checks it on the PR it finds (#224) rather than trusting this line.
    """
    if not may:
        return NO_GRANT
    if "pr" in may:
        what = (f"push `{branch}` to origin and open a pull request for it, "
                "ready for review rather than a draft")
        closes = f" End the pull request body with `Closes #{issue}`." if issue else ""
        rest = ""
    else:
        what = f"push `{branch}` to origin"
        closes = ""
        rest = " Do not open a pull request: `mnemo deliver` does that."
    return (
        f"Do not merge. Once the full test suite passes, {what}, without asking "
        "first: the maintainer granted this when they dispatched you, and this "
        f"is their answer, so do not ask for it again.{closes}{rest} If the "
        "suite does not pass, publish nothing and say what fails. Never "
        "force-push, and publish nothing beyond this branch."
    )


def _wrap(text: str, **indent: str) -> str:
    return textwrap.fill(
        text, width=78, break_on_hyphens=False, break_long_words=False, **indent
    )


def build_prompt(
    issue: int, *, title: str, body: str, repo_root: Path | str | None = None,
    may: grants.Grant = (),
) -> str:
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
    there is no way to pass one here. *repo_root* is context, not approach: it
    only decides whether the repo's changelog convention is stated. *may* is
    a permission, not an approach either: what the maintainer already approved
    publishing, never how to build the thing published (#317).
    """
    branch = branch_name(issue)
    return _PROMPT.format(
        issue=issue,
        title=title or f"issue #{issue}",
        body=(body or "").strip() or "(empty — read it with gh)",
        branch=branch,
        changelog=_changelog_prompt(str(issue), repo_root),
        publish=_wrap(
            _publish_clause(may, branch=branch, issue=issue),
            initial_indent="- ", subsequent_indent="  ",
        ),
        closing=_closing_clause(may),
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
{changelog}
{publish}
{closing}"""

_CONSUMES_PROMPT = """**What you may assume exists** — another piece is delivering
these. They may not exist in your worktree yet: write against the signature,
stub locally if you must, and the merge resolves it. Do not wait for them, and
do not implement them yourself:
{items}

"""


def build_piece_prompt(
    piece: contracts.Piece, *, feature: str, repo_root: Path | str | None = None,
    may: grants.Grant = (),
) -> str:
    """A contract piece's opening prompt: its boundary and its interfaces.

    Like :func:`build_prompt`, this takes no "approach" parameter, and for the
    same reason (see that function's rationale). The distinction the contract
    relies on is thin but real: *"do not touch X, consume ``Y.parse()``"* is a
    **boundary** and belongs here, while *"use a regex to parse it"* is an
    **approach** and must not be expressible. Blurring the two reintroduces the
    #187 failure at N children instead of one.

    *may* is what this child was granted, already resolved — the piece's own
    ``may:`` over the dispatch's ``--may`` — by :func:`dispatch_piece`.
    """
    branch = branch_name(piece.slug, feature=feature)
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
        branch=branch,
        changelog=_changelog_prompt(f"{feature}-{piece.slug}", repo_root),
        # Unwrapped when nothing was granted: that line predates the grant and
        # stays byte-identical, one 80-column line and all.
        publish=(
            f"Run the full test suite before you finish. {NO_GRANT}" if not may
            else _wrap("Run the full test suite before you finish. "
                       + _publish_clause(may, branch=branch))
        ),
        closing=_closing_clause(may),
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

# The parser and the regexes it is made of live in :mod:`mnemo.core.claude_cli`
# next to the *statement* of the `--bg` output shape they implement, so the
# code and its contract cannot drift apart (#235). Re-exported under the old
# names for readers who arrive here from #211.
_SHORT_ID_RE = claude_cli.SHORT_ID_RE
_ANSI_RE = claude_cli.ANSI_RE
_short_id_from = claude_cli.short_id_from

#: The tools a read-only child may not call. Measured 2026-09-17 against the
#: real CLI: the block holds and reaches the child's own subagents, and an
#: unknown name is reported rather than ignored ("matches no known tool").
#: It closes these tools and nothing else — the same measurement showed the
#: child can still write through Bash, so this prevents accident and drift,
#: never intent.
READ_ONLY_TOOLS = ("Edit", "Write", "NotebookEdit")


def spawn_child(
    prompt: str, *, cwd: Path | str, model: str | None = None, lean: bool = True,
    effort: str | None = None, read_only: bool = False,
) -> str:
    r"""Start a detached child in *cwd*. Returns its short id, or ``""``.

    ``--bg`` with the prompt **positional**. Never ``-p``/``--print``: the CLI
    rejects the combination, and ``--print`` would never start the interactive
    session ``claude attach`` needs, leaving the job unattachable. Never
    wrapped in ``timeout``, which is not on the macOS PATH.

    **The child's configuration (#270).** By default the child does *not*
    inherit the maintainer's profile: no plugins, no unrelated MCP servers, no
    unrelated skills or hooks. Only the repo's own ``.claude/`` and mnemo's
    own hooks and MCP server, handed back explicitly. Measured at ~58,000 →
    ~42,000 first-turn input tokens, three children per arm. See
    :mod:`mnemo.core.child_profile` for what is dropped, what is rebuilt and
    why the blunter switches were rejected; ``lean=False`` (or
    ``MNEMO_DISPATCH_FULL_PROFILE=1``) restores the old behaviour for a child
    that genuinely needs a plugin.

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

    When no token matches, this raises :class:`claude_cli.ContractBroken`
    naming the assumption (``bg-prints-short-id``), the installed
    ``claude --version`` and the bytes that were printed — never a guess, and
    no longer a silent ``""`` either (#235). ``background.`` reads as an id and
    sends the maintainer to a command that cannot work; a blank column reads
    as missing and says nothing about *why*. The orchestration below catches
    it and keeps the worktree, because a zero exit means the child most likely
    started regardless.

    **The model (#268).** *model* is passed through as ``--model <id>``, and
    omitted entirely when it is ``None`` — so a dispatch that names no model
    runs the byte-identical command it ran before, and the child resolves
    whatever ``~/.claude/settings.json`` says. ``--bg`` and ``--model``
    compose (``bg-model-flag``, measured 2026-09-14 on 2.1.270: a child
    spawned with ``--model haiku`` produced a transcript whose every
    assistant record reads ``claude-haiku-4-5-20251001``).

    The value is **not** validated against a list of known model ids here.
    Claude Code accepts aliases (``opus``, ``haiku``), full ids, and
    provider-prefixed names, and the set changes without mnemo; a local
    allowlist would refuse a model that works. An unknown id fails in the
    child's own startup, where the error names the real vocabulary.

    **The effort (#351).** *effort* is passed as ``--effort <level>`` and
    omitted when ``None``, exactly like the model (``bg-effort-flag``). It
    *is* checked against :data:`claude_cli.EFFORT_LEVELS`, and refused before
    anything runs: the CLI's vocabulary is closed, and an unknown level does
    not fail in the child — it is ignored with a warning and the child runs on
    the default, which is the one outcome a caller who asked for ``max``
    cannot notice.

    **Read-only (#371).** *read_only* closes :data:`READ_ONLY_TOOLS` —
    ``Edit``, ``Write``, ``NotebookEdit`` — via ``--disallowedTools``, for a
    child meant to investigate and report rather than build. The block
    reaches the child's own subagents (measured 2026-09-17), so a read-only
    dispatch cannot launder an edit through a Task. It does **not** close
    ``Bash``: the same measurement showed the child can still write through
    it, so this flag prevents accident and drift, never a determined child.
    When *read_only* is false (the default), the argv is byte-identical to a
    dispatch that never heard of this flag.

    ``--disallowedTools`` is variadic and space-separated: it swallows every
    token after it up to the next flag, so the tool list is closed with an
    explicit ``--`` rather than relying on ``--model``/``--effort``/lean's own
    flags to happen to follow it. That reliance is real: ``lean=False`` with
    no model gives it nothing to swallow but the prompt, which is then read as
    a tool name and the child starts with no instructions at all (measured
    2026-09-17 — the run died with "Input must be provided either through
    stdin or as a prompt argument"). ``--`` was confirmed against the real CLI
    to end the list correctly wherever it is placed.
    """
    if effort and effort not in claude_cli.EFFORT_LEVELS:
        raise DispatchError(
            f"unknown effort {effort!r}: "
            f"use one of {', '.join(claude_cli.EFFORT_LEVELS)}"
        )
    args = ["claude", "--bg"]
    if read_only:
        # `--disallowedTools` is variadic and space-separated, so it consumes
        # every following token until a flag. `--` ends it explicitly: relying
        # on a later flag to stop it is relying on `--model`/`--effort`/lean
        # happening to be present, and `--full-profile` with no model makes
        # the prompt the next token, where it is read as a tool name and the
        # child starts with no instructions at all (measured 2026-09-17).
        args += ["--disallowedTools", *READ_ONLY_TOOLS, "--"]
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    if child_profile.is_lean(lean):
        # Before the prompt, which is positional: a flag after it would be
        # read as a second positional by any CLI that stops parsing there.
        # Alongside --model, never instead of it: the two choose different
        # things (who the child is, and what it loads) and both survive.
        args += child_profile.lean_args(cwd)
    args.append(prompt)
    try:
        result = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    except (FileNotFoundError, OSError) as exc:
        raise DispatchError(f"could not spawn claude: {exc}") from exc

    if result.returncode != 0:
        raise DispatchError(
            f"claude --bg failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return claude_cli.require_short_id(result.stdout)


# --- orchestration ---------------------------------------------------------

Fetcher = Callable[..., Issue]


def _spawn_into(
    target: Target, tree: Path, prompt: str, *, repo_root: Path | str, branch: str,
    model: str | None = None,
    lean: bool = True,
    may: grants.Grant = (),
    effort: str | None = None,
) -> Dispatched:
    """Spawn *prompt*'s child in *tree*, rolling the tree back only if none started.

    Two failure shapes, treated differently on purpose:

    - ``claude`` could not run, or exited nonzero: no child exists, so the
      tree and the branch are removed and the error propagates. Including
      ``KeyboardInterrupt``: a Ctrl-C between the two steps must not leave a
      tree — or a branch — that blocks the retry.
    - ``claude`` exited zero but printed nothing shaped like an id
      (:class:`claude_cli.ContractBroken`): a child is almost certainly
      running in *tree*, and removing the directory from under it would be
      the worse outcome. The tree is kept, ``short_id`` is empty, and the
      broken assumption travels on ``warning`` so the report can say which
      observable changed and against which ``claude --version``.

    A spawn that did read an id back is then checked against the jobs dir
    (``jobs-state-json``); a mismatch there is likewise a warning, since the
    child is running either way.
    """
    try:
        short_id = spawn_child(prompt, cwd=tree, model=model, lean=lean, effort=effort)
    except claude_cli.ContractBroken as exc:
        return Dispatched(
            issue=target, worktree=tree, short_id="", warning=str(exc),
            model=model, effort=effort, may=may,
        )
    except BaseException:
        remove_worktree(tree, repo_root=repo_root, branch=branch)
        raise
    warning = claude_cli.verify_registered(short_id, cwd=tree)
    # Here, per child, rather than once the whole list is back: a Ctrl-C on
    # the third issue must not cost the first two their link (#288). Only a
    # child whose id was read back can be looked up by it, hence after the
    # ContractBroken branch above.
    parents.record(short_id)
    # The prompt already granted it; this only lets the queue say so (#317).
    grants.record(short_id, may)
    return Dispatched(
        issue=target, worktree=tree, short_id=short_id, warning=warning,
        model=model, effort=effort, may=may,
    )


def dispatch_issue(
    issue: int, *, repo_root: Path | str, fetch: Fetcher = fetch_issue,
    model: str | None = None,
    lean: bool = True,
    may: grants.Grant = (),
    effort: str | None = None,
) -> Dispatched:
    """Dispatch one issue: read it, make its tree, spawn its child.

    Ordered so that the cheapest refusal comes first — a nonexistent issue
    never reaches git — and so that anything created is removed again if a
    later step fails before a child exists. Raises :class:`DispatchError` with
    no state left behind; see :func:`_spawn_into` for the one case that keeps
    the tree.
    """
    details = fetch(issue, repo_root=repo_root)  # before any git state exists
    tree = ensure_worktree(issue, repo_root=repo_root)
    return _spawn_into(
        issue, tree,
        build_prompt(issue, title=details.title, body=details.body,
                     repo_root=repo_root, may=may),
        repo_root=repo_root, branch=branch_name(issue), model=model, lean=lean,
        may=may, effort=effort,
    )


def dispatch_all(
    issues: Sequence[int], *, repo_root: Path | str, fetch: Fetcher = fetch_issue,
    model: str | None = None,
    lean: bool = True,
    may: grants.Grant = (),
    effort: str | None = None,
) -> list[Dispatched]:
    """Dispatch each issue, independently. One failure never strands the rest.

    Children are independent by construction — separate trees, separate
    branches — so a bad issue number in the middle of a list is reported and
    skipped rather than aborting the ones that would have worked.

    *model* applies to every child of this invocation. There is no per-issue
    model here on purpose: an issue is a unit of work, not a unit of budget,
    and nothing on an issue says how hard it is (#268). A contract piece is
    the one place a per-child model exists, because a contract is written and
    reviewed before dispatch and can say so per piece. *effort* is the same
    (#351).
    """
    out: list[Dispatched] = []
    for issue in issues:
        try:
            out.append(
                dispatch_issue(
                    issue, repo_root=repo_root, fetch=fetch,
                    model=model, lean=lean, may=may, effort=effort,
                )
            )
        except DispatchError as exc:
            out.append(Dispatched(issue=issue, error=str(exc)))
    return out


def dispatch_piece(
    piece: contracts.Piece, *, feature: str, repo_root: Path | str,
    model: str | None = None,
    lean: bool = True,
    may: grants.Grant = (),
    effort: str | None = None,
) -> Dispatched:
    """Dispatch one contract piece: make its tree, spawn its child.

    The mirror of :func:`dispatch_issue` with the fetch step already done —
    the contract was read and validated in one pass before any of this ran.

    *feature* reaches :func:`ensure_worktree` because it is what names the
    branch (``feat/<feature>/<slug>``); the target alone cannot.

    *model* is the dispatch-wide default; the piece's own ``model:`` wins over
    it. That direction is the point: the contract was written and reviewed
    knowing what each piece is, and the flag is a blanket applied at the
    command line. A blanket that overrode a considered per-piece choice would
    make the field unusable. *effort* resolves the same way, for the same
    reason (#351).

    *may* resolves the same way, with one difference: a piece's ``may: none``
    is ``()``, not absent, and still wins — so a contract can withhold from
    one piece what the flag granted the rest (#317).
    """
    target = f"c-{piece.slug}"
    granted = piece_grant(piece, may)
    tree = ensure_worktree(target, repo_root=repo_root, feature=feature)
    return _spawn_into(
        target, tree,
        build_piece_prompt(piece, feature=feature, repo_root=repo_root, may=granted),
        repo_root=repo_root, branch=branch_name(target, feature=feature),
        model=piece.model or model, lean=lean, may=granted,
        effort=piece.effort or effort,
    )


def piece_grant(piece: contracts.Piece, may: grants.Grant = ()) -> grants.Grant:
    """The grant a piece's child gets: its own ``may:`` if written, else *may*."""
    return may if piece.may is None else piece.may


def dispatch_contract(
    contract: contracts.Contract, *, repo_root: Path | str,
    model: str | None = None, lean: bool = True,
    may: grants.Grant = (),
    effort: str | None = None,
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

    *model* and *effort* are the defaults for every piece that does not name
    its own; see :func:`dispatch_piece` for why the piece wins.
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
                    piece, feature=contract.feature, repo_root=repo_root,
                    model=model, lean=lean, may=may, effort=effort,
                )
            )
        except DispatchError as exc:
            out.append(Dispatched(issue=f"c-{piece.slug}", error=str(exc)))
    return out
