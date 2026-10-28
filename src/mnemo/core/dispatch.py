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
  (``design/specs/2026-09-15-inbox-reply-authority.md``). This module
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

**An early confidence rating was refused** (#383). The proposal was Devin's:
before exploring, the child rates its confidence in the issue, and a low
rating comments and stops instead of building. Measured over the 180 dispatch
children on disk on 2026-09-19 (``tools/measure_refusal_timing.py``,
``design/specs/2026-09-19-dispatch-confidence-timing.md``): 33 recorded a
refusal, a corrected premise or a re-scope, and only **4** stated it within
their first ten tool uses — three of those read it straight out of
``gh issue view --comments``, which ``_PROMPT`` already puts first, and the
fourth then spent 37 more tool uses measuring before it had anything to say.
20 of the 33 said nothing until the closing report. The only child that ever
refused without touching its tree took 29 tool uses to get there, and its
argument ends at a file it had not opened at tool use 3. A rating placed
before the exploration is a rating of the issue's prose, which is the unbacked
claim #306 is about; placed after it, it is the refusal the prompt already
licenses. The other half — "low confidence asks instead of building" — has no
reader: of those 180 children, exactly one received a turn typed by the
maintainer, and it was an instruction at tool use 75, not an answer. That is
#357 from the child's side, and a rating does not fix it.

**A Grok child** (``design/specs/2026-10-16-grok-host.md``). When
``agent_cli.current_cli`` names ``grok``, the same tree, branch, prompt,
grant and parent link are made, and the child is a session of Grok Build's
leader instead of a ``claude --bg`` job: :meth:`acp.Leader.new_session` in the
worktree, under the dispatching process's ``GROK_HOME`` (the account selected
when it was dispatched), with the opening prompt as its first turn
(:func:`_spawn_grok`). Its id, home and CLI are written once, at spawn, to
:mod:`mnemo.core.sessions.child_agents`, and every ledger keys it by
``grok.paths.short_id`` — the *last* eight hex digits, since two UUIDv7 ids
minted together share their first eight. A dormant Grok session fires no
SessionEnd, so its prompt does not end on ``claude stop``: it ends its last
turn on its closing report (:data:`_GROK_CLOSING_STEPS`). Everything Grok
cannot take is refused before a worktree exists (:func:`check_grok`).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple, Union

from mnemo.core import (
    agent_cli, child_priority, child_profile, claude_cli, contracts, redact,
)
from mnemo.core.sessions import child_agents, grants, parents

WORKTREE_SUFFIX = "-wt-"

# `<anything>-wt-<digits>` or `<anything>-wt-c-<slug>`, optional trailing
# slash. Anchored on both ends so `mnemo-wt-feature` — a hand-made worktree
# that is not a dispatch — is not mistaken for one.
#
# The alternation is the whole point: a wildcard `(.+)` would name every
# directory ending in `-wt-<anything>` a dispatch child, and `mnemo sessions`
# would then label an unrelated session as one of ours. Only the shapes this
# module itself writes are admitted — a bare issue number, a contract piece
# under the reserved `c-` prefix, which no hand-made branch name uses, and an
# issue number followed by a twin's tag (#449): exactly six lowercase hex
# digits, which is what :func:`new_twin_tag` draws, so `mnemo-wt-12-old` or
# `mnemo-wt-12-v2` stays a hand-made tree.
_WT_RE = re.compile(r"-wt-(\d+(?:-[0-9a-f]{6})?|c-[a-z0-9-]+)/?$")

# One twin's tag inside what `_WT_RE` captured: `449-3fa9c1` -> `3fa9c1`.
_TWIN_RE = re.compile(r"^(\d+)-([0-9a-f]{6})$")

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
    #: Whether this child was spawned to investigate rather than build
    #: (#371): its file-editing tools are closed and its closing asks for a
    #: comment on the issue, never a push. Set even when ``short_id`` could
    #: not be read back — the ``ContractBroken`` branch of ``_spawn_into`` —
    #: because a child whose id is missing from the queue still loses its
    #: posture there if this field silently reverted to the default.
    read_only: bool = False
    #: The agent CLI the child runs on (``agent_cli.AGENT_CLIS``).
    agent: str = agent_cli.CLAUDE
    #: A Grok child's full session id and the home whose leader hosts it,
    #: which the attach hint needs and ``short_id`` cannot give back. ``None``
    #: for a Claude child, whose job directory is named by its short id.
    session_id: str | None = None
    home: str | None = None
    #: The ``--permission-mode`` the child was started with, ``None`` for
    #: ``--bg``'s ``auto`` (#741), and the words the report prints about it:
    #: why it inherited the parent's class, or why it could not.
    permission_mode: str | None = None
    mode_note: str = ""


# --- naming: the mapping, as a convention ----------------------------------


def worktree_path(
    target: Target, *, repo_root: Path | str, tag: str | None = None,
) -> Path:
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

    *tag* names one of an issue's twins (#449): ``mnemo-wt-449-3fa9c1``. Two
    children of one issue need two trees, and the tag is what tells them
    apart without saying which is which — see :mod:`mnemo.core.twins`.
    """
    root = Path(repo_root)
    base = _WT_RE.sub("", root.name)
    suffix = f"{target}-{_checked_tag(target, tag)}" if tag else f"{target}"
    return root.parent / f"{base}{WORKTREE_SUFFIX}{suffix}"


def new_twin_tag() -> str:
    """A fresh twin tag: six random lowercase hex digits (#449).

    Random rather than ``a``/``b`` or ``1``/``2``: the tag is in the child's
    own ``cwd`` and branch, and an ordinal there would tell the child that
    another run exists and which one it is. Six hex digits say neither.
    """
    import secrets

    return secrets.token_hex(3)


def _checked_tag(target: Target, tag: str) -> str:
    """*tag*, refused unless it is a twin tag on an issue number.

    A tag that ``_WT_RE`` would not read back would name a tree nothing
    recognises as a dispatch — ``deliver`` could not find it, and the queue
    would not label it. Refused here, before any git state exists.
    """
    if not isinstance(target, int) or not re.fullmatch(r"[0-9a-f]{6}", tag or ""):
        raise ValueError(
            f"twin tag {tag!r} on {target!r}: a twin is an issue number "
            "and six lowercase hex digits"
        )
    return tag


def branch_name(
    target: Target, *, feature: str | None = None, tag: str | None = None,
) -> str:
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

    A twin (#449) is ``fix/issue-<n>-<tag>``: git will not check one branch
    out in two trees, so each twin needs its own.
    """
    if tag:
        return f"fix/issue-{target}-{_checked_tag(target, tag)}"
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
    twin = _TWIN_RE.match(captured)
    if twin:
        # A twin is its issue's child like any other: `#449` in the queue, and
        # `Closes #449` on the PR `deliver` opens for it.
        return int(twin.group(1))
    return int(captured) if captured.isdigit() else captured


def twin_tag_for_cwd(cwd: str | Path | None) -> str | None:
    """The twin tag a dispatched worktree encodes (#449), or ``None``.

    ``None`` for every tree that is not a twin's, including an ordinary
    ``-wt-<n>`` child of the same issue.
    """
    if not cwd:
        return None
    match = _WT_RE.search(str(cwd))
    if not match:
        return None
    twin = _TWIN_RE.match(match.group(1))
    return twin.group(2) if twin else None


# --- the prompt: context and scope, never a solution -----------------------

_PROMPT = """Work on issue #{issue} in this repo: {title}

Read the full issue with `gh issue view {issue}` — including its comments,
which often re-scope it. The body as it stands:

---
{body}
---

You are in a git worktree of your own {on_branch}. Work only here.
{siblings}
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


#: The read-only posture's opening prompt. Deliberately parallel to `_PROMPT`:
#: same issue block, same worktree sentence, same refusal licence. What differs
#: is the task (investigate, do not implement) and the closing, which asks for a
#: comment on the issue rather than a check for commits to publish.
_ANALYSIS_PROMPT = """Investigate issue #{issue} in this repo: {title}

Read the full issue with `gh issue view {issue}` — including its comments,
which often re-scope it. The body as it stands:

---
{body}
---

You are in a git worktree of your own on branch `{branch}`. You are here to
find something out, not to build anything: your file-editing tools are closed,
and nothing you learn needs a diff to be worth having.
{siblings}
What is being asked of you:
- Answer the question the issue actually poses. If the issue poses none, say
  what question it should have posed and answer that instead.
- Reach for evidence over inference. Run the code, read the log, count the
  rows. A claim you measured is worth more than a claim you reasoned to.
- Say what you could not determine, and why. An honest gap is a finding.

No approach is prescribed. Decide what the issue actually calls for from the
evidence in the repo. If the issue rests on a premise the code shows to be
wrong, say so and show the evidence — a measured correction is the most
valuable thing you can return.
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


#: The roster block: who else this dispatch started, and nothing else (#384).
#:
#: **What this carries, and what it deliberately does not.** #384 asks for
#: three things to reach the child — what the parent ruled out, what it
#: measured, and which sibling is doing what. Only the third is here.
#:
#: The first two already have a channel that works. Measured on 2026-09-19
#: over the 118 children in ``dispatch-parents.jsonl`` that still have a
#: transcript: **68 of 68** issue children that were handed an opening prompt
#: ran ``gh issue view`` on their own issue — comments included, which is
#: where a re-scoping decision lands — so everything the parent wrote down
#: before dispatching reached every one of them. (The 69th never received a
#: prompt: it is the read-only argv bug #371 fixed.) Passing the parent's
#: *unwritten* reasoning instead means handing a child a conclusion it did not
#: reach, which is the #187 failure verbatim — that child was dispatched with
#: a measured conclusion drawn from its parent ("the slug is the cheaper
#: signal"), refused it, and was right.
#: :func:`build_prompt` takes no approach for that reason, and a summary of
#: the parent's transcript is an approach with a citation attached.
#:
#: Who else is running is different in kind: it is a fact about the world that
#: exists only at dispatch time, is in no issue, and prescribes nothing. The
#: maintainer already writes it by hand — #382, #383, #384 and #385 each got
#: an identical comment naming the other three — which is the workaround this
#: replaces, not an argument that the need is imagined.
#:
#: **The cost it answers, and where it stops.** Of 36 dispatch batches
#: measured, 30 started two or more children at once and 112 of 118 children
#: had a live sibling. In the 16 multi-child *issue* batches, 3 had two
#: siblings write the same file (``activity/context.py``, ``sessions/jobs.py``,
#: ``friction/backfill.py`` + ``corrections.py``). In the 13 multi-child
#: *contract* batches — where each piece is already told what it may not
#: touch — **none** did. So this is rendered into the issue prompt and
#: deliberately not into :func:`build_piece_prompt`, which says why.
#: ``tools/measure_dispatch_siblings.py`` is that measurement; run it with
#: ``--list``, which prints every colliding pair, before quoting the rate.
#: Two children writing one file is an upper bound on harm, not a conflict.
_SIBLINGS_PROMPT = """
Other children of this same dispatch are running in parallel right now, each
in a worktree of its own, all branched from the same commit you were:
{items}

This is not an instruction and none of it is yours to do. It is the one thing
that is true only while you are running and is written in no issue: who else
is editing this repo beside you. Where your work would land on theirs, read
theirs first and keep your diff to what your own issue needs.
"""

#: How many siblings are named before the list is cut short.
#:
#: The child's opening prompt is already ~70k tokens, so this block has to be
#: rounding error or it is a regression. Twelve lines at the truncation width
#: below is under 1 KB — roughly 250 tokens, 0.4% of the baseline. The largest
#: batch ever dispatched was 8, so the cap has never yet bitten; it exists so
#: that a future 40-piece contract costs a footnote rather than a page.
_SIBLING_CAP = 12

#: Where one sibling's line is cut. Long enough for an issue title or a short
#: file list, short enough that twelve of them stay a footnote.
_SIBLING_WIDTH = 96


def _sibling_line(label: str, detail: str) -> str:
    """One roster row, ``- <label> — <detail>``, truncated to one line.

    Newlines are stripped rather than wrapped: a row that spilled onto a
    second line at column 0 would read as prose about the child's own task,
    which is the one thing this block must never be mistaken for.
    """
    detail = " ".join((detail or "").split())
    line = f"- {label}" + (f" — {detail}" if detail else "")
    if len(line) > _SIBLING_WIDTH:
        line = line[: _SIBLING_WIDTH - 1].rstrip() + "…"
    return line


def _siblings_clause(rows: Sequence[tuple[str, str]]) -> str:
    """The roster, or the empty string when this child has no siblings.

    Empty is load-bearing: a one-issue dispatch must render the prompt it
    rendered before this existed, byte for byte, and
    ``tests/unit/test_dispatch_siblings.py`` pins that.

    Redacted on the way out. Nothing here is transcript — issue titles and the
    contract's own file list — so in practice :func:`redact.redact` finds
    nothing to do, and that is the point: #384's boundary says whatever new
    material reaches a child passes through it, and a rule that only holds
    while the material happens to be safe is not a rule.
    """
    if not rows:
        return ""
    shown = [_sibling_line(label, detail) for label, detail in rows[:_SIBLING_CAP]]
    hidden = len(rows) - len(shown)
    if hidden > 0:
        shown.append(f"- (and {hidden} more)")
    text = _SIBLINGS_PROMPT.format(items="\n".join(shown))
    return redact.redact(text)[0]


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

#: The read-only closing. Steps 1 and 3 are `_CLOSING_STEPS`' own, word for
#: word: the report and the stop do not depend on the posture. Step 2 is the
#: difference — a finding goes to the issue, because the worktree it was found
#: in is removed and a briefing alone is read by whoever runs `mnemo sessions`.
_READ_ONLY_CLOSING_STEPS = (
    _CLOSING_STEPS[0],
    "Post your finding as a comment on the issue: `gh issue comment {issue} "
    "--body-file -`, with the body on stdin. Lead with the answer, then the "
    "evidence for it. If you could not reach `gh`, say so in your report — it "
    "is the only other copy.",
    _CLOSING_STEPS[2],
)

#: A Grok child's closing (the Grok round). Grok has no ``claude stop`` and no
#: SessionEnd to count on: a session left idle when its client goes is
#: unloaded as dormant and fires none (measured on grok 1.0.60). What marks
#: it finished is the turn it ends on its report, so the report moves to the
#: end and the stop goes. The publish check is `_CLOSING_STEPS`' own, word for
#: word, so the grant reads the same on both CLIs.
_GROK_CLOSING_STEPS = (
    _CLOSING_STEPS[1],
    "End your last turn with your closing report — what you did, what you "
    "decided and why, what you refused — and write nothing after it. This is "
    "the only copy: it becomes the session's briefing, and the turn that ends "
    "on it is how mnemo knows you finished. Do not close the session "
    "yourself; ending that turn is the whole of finishing.",
)

#: The read-only closing on Grok: the comment, then the report, last.
_GROK_READ_ONLY_CLOSING_STEPS = (
    _READ_ONLY_CLOSING_STEPS[1],
    _GROK_CLOSING_STEPS[1],
)

_CLOSING_HEADING = "\nHow to finish, whatever you decided:\n"


def _closing_clause(
    may: grants.Grant = (), *, read_only: bool = False, issue: Target | None = None,
    agent: str = agent_cli.CLAUDE,
) -> str:
    """The end-of-life instructions every child gets, grant or no grant.

    *may* only decides how step 2 is phrased — whether publishing is something
    this child may do unasked. The report and the stop do not depend on it:
    a child that refused the task has no commits and publishes nothing, but
    its reasoning is the most valuable briefing in the system, because no diff
    carries it.

    Each step is wrapped under its own number, so a continuation never starts
    at column 0 where it would read as a new section.

    *read_only* swaps step 2 for a comment on *issue*: a read-only child has
    nothing to publish, and the check for commits ahead would be asking it to
    look for something the posture guarantees is not there.

    *agent* ``grok`` takes :data:`_GROK_CLOSING_STEPS` instead: the same
    publish check, then the report as the last thing the turn says, and no
    Claude command anywhere.
    """
    # `.format()` unconditionally, never guarded on a placeholder being
    # present: it collapses `{{` to `{` whether or not it substitutes
    # anything, and `_CLOSING_STEPS[2]` relies on that to render
    # `${CLAUDE_CODE_SESSION_ID:0:8}`. An extra kwarg is harmless; a skipped
    # call silently doubles those braces.
    grok = agent == agent_cli.GROK
    if read_only:
        template = _GROK_READ_ONLY_CLOSING_STEPS if grok else _READ_ONLY_CLOSING_STEPS
        steps_source = tuple(step.format(issue=issue) for step in template)
    else:
        if "pr" in may:
            hint = ", push it and open the pull request you were granted"
        elif "push" in may:
            hint = ", push it (do not open a pull request)"
        else:
            hint = ", say so in your report and leave it for `mnemo deliver`"
        template = _GROK_CLOSING_STEPS if grok else _CLOSING_STEPS
        steps_source = tuple(step.format(publish_hint=hint) for step in template)
    steps = "\n".join(
        _wrap(step, initial_indent=f"{number}. ", subsequent_indent="   ")
        for number, step in enumerate(steps_source, start=1)
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


#: Where a blind twin is told it works (#449), in place of its branch's name.
#: The one sentence of the issue prompt that names something only one child
#: has: two twins cannot share a branch, so the name would make their prompts
#: differ, and would name the tag that tells them apart.
_BLIND_BRANCH = "on a branch of its own"


def build_prompt(
    issue: int, *, title: str, body: str, repo_root: Path | str | None = None,
    may: grants.Grant = (), read_only: bool = False,
    siblings: Sequence[Issue] = (),
    blind: bool = False,
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

    *siblings* is the same kind of thing again, and the narrowest of the
    three: the other issues this one run started, so the child knows which of
    them is live while it edits (#384). It is a roster, not a reading — see
    :data:`_SIBLINGS_PROMPT` for why the parent's own reasoning stays out.
    This issue is filtered from its own roster, so a caller may pass the whole
    batch without trimming it.

    *blind* renders the prompt a pair of twins shares byte for byte (#449):
    the branch is not named, because each twin has its own. Nothing else
    changes, and nothing says another run exists. Without it the prompt is
    the one every issue child was given before twins existed.

    A Grok child's prompt is this one with its closing swapped
    (:func:`for_agent`) — deliberately not a parameter here, so the set of
    things a caller can pass stays the set ``test_dispatch_plan`` pins.
    """
    branch = branch_name(issue)
    roster = _siblings_clause(
        [(f"#{other.number}", other.title) for other in siblings
         if other.number != issue],
    )
    if read_only:
        return _ANALYSIS_PROMPT.format(
            issue=issue,
            title=title or f"issue #{issue}",
            body=(body or "").strip() or "(empty — read it with gh)",
            branch=branch,
            siblings=roster,
            closing=_closing_clause(read_only=True, issue=issue),
        )
    return _PROMPT.format(
        issue=issue,
        title=title or f"issue #{issue}",
        body=(body or "").strip() or "(empty — read it with gh)",
        branch=branch,
        on_branch=_BLIND_BRANCH if blind else f"on branch `{branch}`",
        siblings=roster,
        changelog=_changelog_prompt(str(issue), repo_root),
        publish=_wrap(
            _publish_clause(may, branch=branch, issue=issue),
            initial_indent="- ", subsequent_indent="  ",
        ),
        closing=_closing_clause(may),
    )


def for_agent(
    prompt: str, agent: str, *, may: grants.Grant = (), read_only: bool = False,
    issue: Target | None = None,
) -> str:
    """*prompt* as *agent*'s child reads it: the same, with *agent*'s closing.

    Every opening prompt ends on :func:`_closing_clause`, so the closing it
    was rendered with is swapped for *agent*'s and nothing else is touched —
    a Grok child is told the same issue, tree, grant and scope a Claude child
    is (the Grok round's contract). *may*, *read_only* and *issue* must be
    what *prompt* was rendered with; a prompt that does not end on that
    closing raises ``ValueError`` rather than gaining a second one. Claude
    returns *prompt* unchanged.
    """
    if agent == agent_cli.CLAUDE:
        return prompt
    rendered = _closing_clause(may, read_only=read_only, issue=issue)
    if not prompt.endswith(rendered):
        raise ValueError("the prompt does not end on the closing it was rendered with")
    return prompt[: -len(rendered)] + _closing_clause(
        may, read_only=read_only, issue=issue, agent=agent,
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

Before you finish, run the mutation pass a reviewer would otherwise run on your
piece: for each test you added, break the code it guards — invert a condition,
drop a call, return early — run that test, watch it fail, then restore the
code. A test that still passes against the broken code guards nothing; fix it
before you stop. Say in your report which mutations you ran and which test
caught each.
{changelog}
{publish}
{closing}"""

#: Said after a granted piece's publish clause (#771). The closing's step 2
#: already says to push, and #616's child still ran ``git commit … ; claude
#: stop`` and left the parent to push and open the PR. Not said without a
#: grant: there the line it would follow is "do not push without asking".
PIECE_PUSH = (
    " Push before you stop: a commit left only in this worktree reaches no "
    "one, and the notice your parent gets when you stop says so."
)

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

    **A roster of the other pieces was measured and declined** (#384). Issue
    children get one, because they are told nothing about each other and 3 of
    16 multi-child issue batches had two siblings write the same file. A piece
    is already told what it may not touch, and 0 of 13 multi-child contract
    batches collided — so naming the owners would buy nothing measured, while
    putting another piece's files inside this prompt weakens the one thing
    that makes the boundary hard: that they are not in it.

    **The mutation pass and the push are asked here** (#771). Reading the
    commits on the 17 pieces since 2026-09-29 that were not hands-off (#768),
    the parent pushed commits on top of 11 before landing; three of those were
    tests for mutation-pass survivors the child could have found itself, and
    one piece committed and stopped without pushing. Both are checks on the
    child's own work, not an approach to it.

    A Grok child's is this one through :func:`for_agent`, as for an issue.
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
                       + _publish_clause(may, branch=branch) + PIECE_PUSH)
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
            cwd=str(repo_root), capture_output=True, text=True, encoding="utf-8", errors="replace",
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
    issue: Target, *, repo_root: Path | str, feature: str | None = None,
    tag: str | None = None, base: str | None = None,
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

    *tag* makes the tree one of an issue's twins (#449), and *base* is the
    commit to branch from instead of the repo's ``HEAD``: two twins started
    one after the other must start from the same commit even if something
    lands in between.
    """
    target = worktree_path(issue, repo_root=repo_root, tag=tag)
    # before the path is touched
    branch = branch_name(issue, feature=feature, tag=tag)

    if target.exists():
        raise DispatchError(
            f"{target} already exists — refusing to reuse another session's tree"
        )

    try:
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(target),
             *([base] if base else [])],
            cwd=str(repo_root), capture_output=True, text=True, encoding="utf-8", errors="replace",
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
            cwd=str(repo_root), capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, OSError):
        pass
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        subprocess.run(
            ["git", "worktree", "prune"], cwd=str(repo_root),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, OSError):
        pass
    if branch:
        try:
            subprocess.run(
                ["git", "branch", "-d", branch], cwd=str(repo_root),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
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
    settings: Mapping[str, Any] | None = None,
    agent: str = agent_cli.CLAUDE, home: Path | str | None = None,
    permission_mode: str | None = None,
) -> str:
    r"""Start a detached child in *cwd*. Returns its short id, or ``""``.

    *agent* ``grok`` starts it in Grok's leader instead and returns the
    **full** session id (:func:`_spawn_grok`; *home* is the Grok home, *lean*
    and *settings* are Claude's and ignored). Everything below is the Claude
    path, which an omitted *agent* leaves byte-identical.

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
    token after it up to the next flag. Two placements were measured against
    the real CLI on 2026-09-17, and both halves matter.

    Relying on a later flag to end the list is not enough. ``lean=False`` with
    no model leaves nothing between the list and the prompt, so the prompt is
    read as a tool name and the run dies with "Input must be provided either
    through stdin or as a prompt argument" — the child starts with no
    instructions at all.

    But ``--`` cannot sit right after the list either, because it ends *all*
    flag parsing rather than just this one list. Placed there it turns
    ``--setting-sources`` and every lean flag after it into positionals, and
    the child reads ``--setting-sources`` as its prompt. That is not
    hypothetical: the first live read-only dispatch produced a transcript whose
    only user turn was the literal string ``--setting-sources``.

    So the list is emitted early and ``--`` goes last, immediately before the
    prompt: the flags in between stay flags, and the prompt is the prompt
    however few of them are present.

    **Extra settings (#453).** *settings* are keys for the child's
    ``--settings`` file — a twin's ``autoMemoryEnabled: false``. On a lean
    child they join mnemo's hooks in the one file the lean profile already
    passes; on the full profile they are the file's only content. ``None``
    (every caller but twins) leaves the argv byte-identical.

    **Below-normal CPU (#723).** On Windows the settings also carry
    ``env: {MNEMO_CHILD_PRIORITY: below_normal}``, merged with any keys given,
    and mnemo's ``SessionStart`` hook lowers the child's own session from
    inside it (:mod:`mnemo.core.child_priority` says why not here).
    ``dispatch.childPriority: normal`` in the config writes no marker.

    **The permission mode (#741).** *permission_mode* is passed as
    ``--permission-mode <mode>`` and omitted when ``None``, so the child runs
    ``--bg``'s own ``auto`` exactly as before. The caller chooses it
    (:func:`mnemo.core.child_mode.choose`): ``bypassPermissions`` for a child
    of a bypass parent, nothing otherwise. Grok has no such mode and never
    gets it.
    """
    if agent == agent_cli.GROK:
        return _spawn_grok(prompt, cwd=cwd, model=model, effort=effort,
                           read_only=read_only, home=home)
    if effort and effort not in claude_cli.EFFORT_LEVELS:
        raise DispatchError(
            f"unknown effort {effort!r}: "
            f"use one of {', '.join(claude_cli.EFFORT_LEVELS)}"
        )
    args = ["claude", "--bg"]
    if permission_mode:
        args += ["--permission-mode", permission_mode]
    if read_only:
        # Early, so the flags below are between the variadic tool list and the
        # prompt. The `--` that closes the list goes immediately before the
        # prompt instead of here: `--` ends *all* flag parsing, not just this
        # list, so placed here it turns `--setting-sources` and everything
        # after into positionals — the child then reads `--setting-sources` as
        # its prompt and never sees the real one (measured 2026-09-17 on a live
        # dispatch, whose transcript's only user turn was the literal string
        # `--setting-sources`).
        args += ["--disallowedTools", *READ_ONLY_TOOLS]
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    from mnemo.core import config

    settings = child_priority.with_marker(settings, config.load_config())
    if child_profile.is_lean(lean):
        # Before the prompt, which is positional: a flag after it would be
        # read as a second positional by any CLI that stops parsing there.
        # Alongside --model, never instead of it: the two choose different
        # things (who the child is, and what it loads) and both survive.
        args += child_profile.lean_args(cwd, settings)
    elif settings:
        args += ["--settings", str(child_profile.write_settings(cwd, settings))]
    if read_only:
        # Last, immediately before the prompt. `--` ends flag parsing, so it
        # both closes `--disallowedTools`' variadic list and guarantees the
        # prompt is read as the prompt however few flags happen to sit between
        # them — `lean=False` with no model leaves none at all.
        args.append("--")
    args.append(prompt)
    try:
        result = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError) as exc:
        raise DispatchError(f"could not spawn claude: {exc}") from exc

    if result.returncode != 0:
        raise DispatchError(
            f"claude --bg failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return claude_cli.require_short_id(result.stdout)


# --- grok ------------------------------------------------------------------

#: Seconds Grok's leader gets to take a child's opening turn. Taking it is
#: quick — 0.5 s live, on a dormant session that had to be loaded first
#: (grok-acp, PR #710) — and waiting for the turn to *end* is never done: the
#: leader runs it with no client attached.
GROK_TURN_TIMEOUT = 120.0

#: Where Grok keeps the model list its account was offered, refreshed by
#: ``grok`` itself: ``{"models": {<id>: {"info": {"id", "hidden",
#: "supports_reasoning_effort", "reasoning_efforts": [{"value"}…]}}}}``. Read
#: off a real home on grok 1.0.60 (2026-10-16): one model, ``grok-4.7``,
#: taking ``xhigh``, ``high``, ``medium`` and ``low``. Other keys in it
#: (credentials among them) are never read.
GROK_MODELS_CACHE = "models_cache.json"

class _GrokTurnPending(Exception):
    """The session exists but the leader had not taken its opening turn in time."""

    def __init__(self, session_id: str, message: str):
        super().__init__(message)
        self.session_id = session_id


@dataclass(frozen=True)
class GrokVocabulary:
    """What one Grok home's account can run, as Grok itself last recorded it.

    ``models`` maps a model id to the efforts it takes — ``()`` for a model
    that takes none, ``None`` when unknown (a custom model from
    ``config.toml``, which the cache does not describe). ``default`` is the
    model a session gets when none is named, when it can be told.
    """

    models: Dict[str, Optional[Tuple[str, ...]]]
    default: Optional[str]


_TOML_TABLE_RE = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*(?:#.*)?$")
_TOML_DEFAULT_RE = re.compile(r"""^\s*default\s*=\s*["']([^"']+)["']""")


def _custom_models(config_text: str) -> Tuple[Dict[str, None], Optional[str]]:
    """``[model.<name>]`` tables and ``[models] default`` out of a ``config.toml``.

    By pattern rather than a TOML parser: the package supports Python 3.8,
    which has no ``tomllib``, and these two shapes are all that is read.
    """
    names: Dict[str, None] = {}
    default: Optional[str] = None
    table = ""
    for line in config_text.splitlines():
        header = _TOML_TABLE_RE.match(line)
        if header:
            table = header.group(1).strip()
            if table.startswith("model."):
                names[table[len("model."):].strip().strip("\"'")] = None
            continue
        if table == "models":
            found = _TOML_DEFAULT_RE.match(line)
            if found:
                default = found.group(1)
    return names, default


def grok_vocabulary(home: Path | str) -> GrokVocabulary | None:
    """The models and efforts *home* can run, or ``None`` when Grok left no list.

    ``None`` is not a refusal: a home that never fetched its model list (or
    whose cache this version cannot read) is checked by the leader itself at
    spawn — :meth:`acp.Leader.new_session` refuses an unknown model, and the
    tree made for it is rolled back.
    """
    home = Path(home)
    try:
        data = json.loads((home / GROK_MODELS_CACHE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = data.get("models") if isinstance(data, dict) else None
    if not isinstance(raw, dict) or not raw:
        return None
    models: Dict[str, Optional[Tuple[str, ...]]] = {}
    for key, entry in raw.items():
        info = entry.get("info") if isinstance(entry, dict) else None
        if not isinstance(info, dict):
            continue
        model_id = str(info.get("id") or key)
        if info.get("supports_reasoning_effort") is False:
            efforts: Optional[Tuple[str, ...]] = ()
        else:
            listed = info.get("reasoning_efforts")
            efforts = tuple(
                str(e.get("value") or e.get("id")) for e in listed
                if isinstance(e, dict) and (e.get("value") or e.get("id"))
            ) if isinstance(listed, list) else None
        models[model_id] = efforts
        if key != model_id:
            models[str(key)] = efforts
    if not models:
        return None
    try:
        custom, default = _custom_models((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        custom, default = {}, None
    for name in custom:
        models.setdefault(name, None)
    if default is None:
        shown = {e.get("info", {}).get("id") or k for k, e in raw.items()
                 if isinstance(e, dict) and not (e.get("info") or {}).get("hidden")}
        default = next(iter(shown)) if len(shown) == 1 else None
    return GrokVocabulary(models=models, default=default)


def check_grok(
    model: str | None = None, effort: str | None = None, *,
    home: Path | str | None = None, read_only: bool = False,
) -> Path:
    """Refuse what a Grok child cannot be, before any worktree exists.

    Returns the Grok home the child will run under — *home*, else the
    dispatching process's (``$GROK_HOME``, else ``~/.grok``). Raises
    :class:`DispatchError` saying what to do when: ``grok`` is not on ``PATH``; the home
    does not exist or holds no login; *effort* is not one Grok parses; or
    the home's own model list (:func:`grok_vocabulary`) has no *model*, or
    the model it would run does not take *effort*.

    *read_only* is no reason to refuse (#718): :func:`_spawn_grok` marks the
    session and mnemo's PreToolUse hook closes its editing tools.

    A Claude model name (``haiku``, ``opus``) is refused here by the same
    rule: it is not in Grok's list. Grok itself would not refuse it — it
    swaps an unknown model for its default without a word (grok-acp, PR
    #710) — which is why a list is checked at all.
    """
    from mnemo.core.grok import acp
    from mnemo.core.grok import paths as grok_paths

    if effort and effort not in acp.EFFORTS:
        raise DispatchError(
            f"--effort: Grok takes no effort {effort!r}; it takes {', '.join(acp.EFFORTS)}"
        )
    resolved = Path(home).expanduser() if home is not None else grok_paths.grok_home()
    if shutil.which("grok") is None:
        raise DispatchError(
            "grok is not on PATH: install Grok Build (xAI's `grok` CLI) and make "
            "sure `grok --version` runs in this shell, or dispatch with --agent claude"
        )
    if not resolved.is_dir():
        raise DispatchError(
            f"Grok home {resolved} does not exist: log in with "
            f"`GROK_HOME={resolved} grok login`, or select another account"
        )
    if not acp.has_login(resolved):
        raise DispatchError(
            f"Grok home {resolved} has no login: run `GROK_HOME={resolved} grok login`"
        )
    vocabulary = grok_vocabulary(resolved)
    if vocabulary is None:
        return resolved
    if model and model not in vocabulary.models:
        offered = ", ".join(sorted(vocabulary.models)) or "nothing"
        raise DispatchError(
            f"--model: Grok has no model {model!r} for {resolved}; it offers {offered}"
        )
    running = model or vocabulary.default
    takes = vocabulary.models.get(running) if running else None
    if effort and takes is not None and effort not in takes:
        offered = ", ".join(takes) or "no effort setting"
        raise DispatchError(
            f"--effort: Grok's {running} takes no effort {effort!r}; it takes {offered}"
        )
    return resolved


def check_grok_contract(
    contract: contracts.Contract, *, model: str | None = None,
    effort: str | None = None, read_only: bool = False,
    home: Path | str | None = None,
) -> Path:
    """:func:`check_grok` for every piece, as each would be spawned. Before any tree.

    One piece Grok cannot run refuses the whole contract: the pieces were
    decomposed to be built together, and half of them on the wrong footing
    is the merge the contract exists to prevent.

    The dispatch-wide *model* and *effort* are checked only where a piece
    takes them: a ``--model`` every piece overrides spawns nothing.
    """
    resolved = check_grok(home=home)
    for piece in contract.pieces:
        try:
            check_grok(piece.model or model, piece.effort or effort, home=resolved,
                       read_only=piece_read_only(piece, read_only))
        except DispatchError as exc:
            raise DispatchError(f"{piece.slug}: {exc}") from exc
    return resolved


def _spawn_grok(
    prompt: str, *, cwd: Path | str, model: str | None = None,
    effort: str | None = None, read_only: bool = False,
    home: Path | str | None = None,
) -> str:
    """Start a Grok child: a leader session in *cwd* whose first turn is *prompt*.

    Returns the session's full id. The session is created with yolo, so its
    turn runs to its end with nobody attached (measured, grok-acp), and
    :meth:`acp.Leader.prompt` returns once the leader has *taken* the turn,
    never after it ends.

    Raises :class:`DispatchError` when no session could be started — no
    ``grok``, no login, the leader would not come up, a model Grok swapped —
    with the message Grok's client wrote for a person to act on. A session
    that was created but whose turn failed (a rate limit, a login refused
    mid-way) is closed again first, so nothing is left running in a tree
    about to be removed. A turn the leader had not taken within
    :data:`GROK_TURN_TIMEOUT` raises :class:`_GrokTurnPending`: the session
    exists and may still run it, so the caller keeps its tree.
    """
    from mnemo.core.grok import acp

    leader = acp.Leader(Path(home) if home is not None else None)
    prefix = "could not start the Grok child"
    try:
        session_id = leader.new_session(str(cwd), model=model, effort=effort, yolo=True)
    except ValueError as exc:
        raise DispatchError(f"{prefix}: {exc}") from exc
    except TimeoutError as exc:
        raise DispatchError(
            f"{prefix}: {exc}; `mnemo grok roster --home {leader.home}` shows "
            "whether its leader answers"
        ) from exc
    except acp.GrokUnavailable as exc:
        raise DispatchError(f"{prefix}: {exc}") from exc

    if read_only:
        # Before the opening turn, so not one of its calls runs unmarked.
        # Grok has no per-session tool list; mnemo's PreToolUse hook denies a
        # marked session's editing tools (#718, :mod:`mnemo.core.grok.read_only`).
        from mnemo.core.grok import read_only as grok_read_only

        try:
            grok_read_only.mark(session_id, cwd)
        except Exception as exc:  # noqa: BLE001
            _close_quietly(leader, session_id)
            raise DispatchError(
                f"{prefix}: its read-only mark could not be written ({exc}), and "
                "without it its editing tools would be open"
            ) from exc

    try:
        leader.prompt(session_id, str(cwd), prompt, wait=False, timeout=GROK_TURN_TIMEOUT)
    except TimeoutError as exc:
        raise _GrokTurnPending(
            session_id,
            f"Grok's leader had not taken the opening turn of {session_id} after "
            f"{GROK_TURN_TIMEOUT:.0f}s; it may still run it — `mnemo grok roster "
            f"--home {leader.home}` shows its activity",
        ) from exc
    except BaseException as exc:
        _close_quietly(leader, session_id)
        if isinstance(exc, acp.RateLimited):
            raise DispatchError(
                f"{prefix}: the account in {leader.home} is past its usage limit "
                f"({exc}); wait for it to reset, or dispatch on another account"
            ) from exc
        if isinstance(exc, acp.GrokUnavailable):
            raise DispatchError(f"{prefix}: {exc}") from exc
        raise
    return session_id


def _close_quietly(leader: Any, session_id: str) -> None:
    try:
        leader.close(session_id)
    except Exception:  # noqa: BLE001 — the error being reported is the one that matters
        pass


# --- orchestration ---------------------------------------------------------

Fetcher = Callable[..., Issue]


def _spawn_into(
    target: Target, tree: Path, prompt: str, *, repo_root: Path | str, branch: str,
    model: str | None = None,
    lean: bool = True,
    may: grants.Grant = (),
    effort: str | None = None,
    read_only: bool = False,
    settings: Mapping[str, Any] | None = None,
    agent: str = agent_cli.CLAUDE,
    home: Path | str | None = None,
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

    *agent* ``grok`` takes :func:`_spawn_grok_into` instead.
    """
    if agent == agent_cli.GROK:
        return _spawn_grok_into(
            target, tree, prompt, repo_root=repo_root, branch=branch, model=model,
            may=may, effort=effort, read_only=read_only, home=home,
        )
    # Only when given, so every other caller hands the chokepoint exactly
    # the call it always did (#453: nothing changes for non-twin children).
    extra: Dict[str, Any] = {"settings": settings} if settings else {}
    from mnemo.core import child_mode, config

    mode = child_mode.choose(config.load_config(), cwd=tree)
    if mode.mode:
        extra["permission_mode"] = mode.mode
    try:
        short_id = spawn_child(
            prompt, cwd=tree, model=model, lean=lean, effort=effort,
            read_only=read_only, **extra,
        )
    except claude_cli.ContractBroken as exc:
        return Dispatched(
            issue=target, worktree=tree, short_id="", warning=str(exc),
            model=model, effort=effort, may=may, read_only=read_only,
            permission_mode=mode.mode, mode_note=mode.note,
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
    # A watcher for this child's stop, in case its SessionEnd never runs
    # (#502). Here as well as at SessionStart because the child's own start
    # can come before the link above is written.
    _ensure_notice_watcher()
    # The prompt already granted it; this only lets the queue say so (#317).
    grants.record(short_id, may)
    return Dispatched(
        issue=target, worktree=tree, short_id=short_id, warning=warning,
        model=model, effort=effort, may=may, read_only=read_only,
        permission_mode=mode.mode, mode_note=mode.note,
    )


def _spawn_grok_into(
    target: Target, tree: Path, prompt: str, *, repo_root: Path | str, branch: str,
    model: str | None = None, may: grants.Grant = (), effort: str | None = None,
    read_only: bool = False, home: Path | str | None = None,
) -> Dispatched:
    """:func:`_spawn_into` for a Grok child. The same two failure shapes.

    No session: the tree and the branch are removed and the error propagates.
    A session whose opening turn was not seen taken (:class:`_GrokTurnPending`)
    keeps its tree, is recorded like any other child, and carries the reason
    on ``warning`` — the leader may still run it, as a ``claude --bg`` that
    printed no id most likely still runs.

    Recorded in the order a reader needs: the agent line first (the full id
    and home every other ledger's short id points back to), then the parent
    link, the watcher and the grant, exactly as for Claude.
    """
    from mnemo.core.grok import paths as grok_paths

    resolved = Path(home).expanduser() if home is not None else grok_paths.grok_home()
    warning = None
    try:
        session_id = spawn_child(prompt, cwd=tree, model=model, effort=effort,
                                 read_only=read_only, agent=agent_cli.GROK, home=resolved)
    except _GrokTurnPending as exc:
        session_id, warning = exc.session_id, str(exc)
    except BaseException:
        remove_worktree(tree, repo_root=repo_root, branch=branch)
        raise
    common = dict(issue=target, worktree=tree, model=model, effort=effort, may=may,
                  read_only=read_only, agent=agent_cli.GROK, home=str(resolved))
    if not session_id:
        return Dispatched(short_id="", warning=warning, **common)
    short_id = grok_paths.short_id(session_id)
    child_agents.record(child_agents.ChildAgent(
        short_id=short_id, agent=agent_cli.GROK, session_id=session_id,
        home=str(resolved), cwd=str(tree),
    ))
    parents.record(short_id)
    _ensure_notice_watcher()
    grants.record(short_id, may)
    return Dispatched(short_id=short_id, warning=warning, session_id=session_id, **common)


def _ensure_notice_watcher() -> None:
    """Start ``mnemo child-notices`` unless one runs. Never raises: a missing
    watcher costs a notice that the hook usually sends anyway, never a
    dispatch."""
    try:
        from mnemo.core import config, paths
        from mnemo.core.sessions import child_notices

        cfg = config.load_config()
        child_notices.ensure_watcher(cfg, vault_root=paths.vault_root(cfg))
    except Exception:  # noqa: BLE001
        pass


def dispatch_issue(
    issue: int, *, repo_root: Path | str, fetch: Fetcher = fetch_issue,
    model: str | None = None,
    lean: bool = True,
    may: grants.Grant = (),
    effort: str | None = None,
    read_only: bool = False,
    details: Issue | None = None,
    siblings: Sequence[Issue] = (),
    agent: str = agent_cli.CLAUDE,
    home: Path | str | None = None,
) -> Dispatched:
    """Dispatch one issue: read it, make its tree, spawn its child.

    Ordered so that the cheapest refusal comes first — a nonexistent issue
    never reaches git — and so that anything created is removed again if a
    later step fails before a child exists. Raises :class:`DispatchError` with
    no state left behind; see :func:`_spawn_into` for the one case that keeps
    the tree.

    *details* is the issue already read, which :func:`dispatch_all` has
    because it reads the whole batch before it spawns any of it (#384). Given
    one, *fetch* is not called: re-reading would be a second ``gh`` round-trip
    for an answer already in hand, and a second answer that could differ from
    the one the siblings were told about.

    *agent* ``grok`` spawns the child in Grok's leader under *home*; what
    Grok cannot take is refused by :func:`check_grok` before the tree exists.
    """
    grok = {}
    if agent == agent_cli.GROK:
        home = check_grok(model, effort, home=home, read_only=read_only)
        grok = {"agent": agent, "home": home}
    if details is None:
        details = fetch(issue, repo_root=repo_root)  # before any git state exists
    tree = ensure_worktree(issue, repo_root=repo_root)
    return _spawn_into(
        issue, tree,
        for_agent(
            build_prompt(issue, title=details.title, body=details.body,
                         repo_root=repo_root, may=may, read_only=read_only,
                         siblings=siblings),
            agent, may=may, read_only=read_only, issue=issue,
        ),
        repo_root=repo_root, branch=branch_name(issue), model=model, lean=lean,
        may=may, effort=effort, read_only=read_only, **grok,
    )


def dispatch_all(
    issues: Sequence[int], *, repo_root: Path | str, fetch: Fetcher = fetch_issue,
    model: str | None = None,
    lean: bool = True,
    may: grants.Grant = (),
    effort: str | None = None,
    read_only: bool = False,
    agent: str = agent_cli.CLAUDE,
    home: Path | str | None = None,
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

    **Read the whole batch before spawning any of it** (#384). Every child is
    told which other issues this run started, and that list can only be
    truthful if the issues have been read — a number ``gh`` refuses is not a
    sibling, and naming it would send every other child looking for work
    nobody is doing. Reading first costs nothing extra: the same one ``gh``
    call per issue, moved earlier. It also makes a bad issue number refusable
    before a single worktree exists, rather than after the ones ahead of it
    in the list have already spawned.

    The roster states intent, not outcome: a child whose *spawn* fails after
    its siblings were told about it leaves them naming an issue nobody took.
    That is the cheap direction — a child reads an issue and finds no branch —
    and closing it would mean spawning the batch twice.

    On Grok (*agent*), :func:`check_grok` runs once, first: what it refuses
    — no ``grok``, no login, a model the account does not offer — refuses
    every issue alike, so each is reported failed with its words and no
    issue is read.
    """
    grok = {}
    if agent == agent_cli.GROK:
        try:
            home = check_grok(model, effort, home=home, read_only=read_only)
        except DispatchError as exc:
            return [Dispatched(issue=issue, error=str(exc), agent=agent) for issue in issues]
        grok = {"agent": agent, "home": home}
    batch: list[tuple[int, Issue | None, str]] = []
    for issue in issues:
        try:
            batch.append((issue, fetch(issue, repo_root=repo_root), ""))
        except DispatchError as exc:
            batch.append((issue, None, str(exc)))
    roster = [details for _, details, _ in batch if details is not None]

    out: list[Dispatched] = []
    for issue, details, error in batch:
        if details is None:
            out.append(Dispatched(issue=issue, error=error))
            continue
        try:
            out.append(
                dispatch_issue(
                    issue, repo_root=repo_root, fetch=fetch, details=details,
                    siblings=roster,
                    model=model, lean=lean, may=may, effort=effort,
                    read_only=read_only, **grok,
                )
            )
        except DispatchError as exc:
            out.append(Dispatched(issue=issue, error=str(exc), agent=agent))
    return out


def dispatch_piece(
    piece: contracts.Piece, *, feature: str, repo_root: Path | str,
    model: str | None = None,
    lean: bool = True,
    may: grants.Grant = (),
    effort: str | None = None,
    read_only: bool = False,
    agent: str = agent_cli.CLAUDE,
    home: Path | str | None = None,
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

    *agent* and *home* as in :func:`dispatch_issue`.
    """
    target = f"c-{piece.slug}"
    granted = piece_grant(piece, may)
    grok = {}
    if agent == agent_cli.GROK:
        home = check_grok(piece.model or model, piece.effort or effort, home=home,
                          read_only=read_only)
        grok = {"agent": agent, "home": home}
    tree = ensure_worktree(target, repo_root=repo_root, feature=feature)
    return _spawn_into(
        target, tree,
        for_agent(
            build_piece_prompt(piece, feature=feature, repo_root=repo_root, may=granted),
            agent, may=granted,
        ),
        repo_root=repo_root, branch=branch_name(target, feature=feature),
        model=piece.model or model, lean=lean, may=granted,
        effort=piece.effort or effort, read_only=read_only, **grok,
    )


def piece_grant(piece: contracts.Piece, may: grants.Grant = ()) -> grants.Grant:
    """The grant a piece's child gets: its own ``may:`` if written, else *may*."""
    return may if piece.may is None else piece.may


def piece_read_only(piece: contracts.Piece, read_only: bool) -> bool:
    """The piece's own posture, or the dispatch's when it declares none."""
    return read_only if piece.read_only is None else piece.read_only


def dispatch_contract(
    contract: contracts.Contract, *, repo_root: Path | str,
    model: str | None = None, lean: bool = True,
    may: grants.Grant = (),
    effort: str | None = None,
    read_only: bool = False,
    agent: str = agent_cli.CLAUDE,
    home: Path | str | None = None,
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

    On Grok (*agent*), every piece is checked (:func:`check_grok_contract`)
    before the first tree exists, and one piece Grok cannot run raises
    :class:`DispatchError` for the whole contract.
    """
    if not contract.is_dispatchable:
        raise DispatchError(
            f"contract verdict is {contract.verdict!r}, not 'parallel' — "
            "this work does not divide; run it in one session"
        )
    grok = {}
    if agent == agent_cli.GROK:
        home = check_grok_contract(contract, model=model, effort=effort,
                                   read_only=read_only, home=home)
        grok = {"agent": agent, "home": home}

    out: list[Dispatched] = []
    for piece in contract.pieces:
        try:
            out.append(
                dispatch_piece(
                    piece, feature=contract.feature, repo_root=repo_root,
                    model=model, lean=lean, may=may, effort=effort,
                    read_only=piece_read_only(piece, read_only), **grok,
                )
            )
        except DispatchError as exc:
            out.append(Dispatched(issue=f"c-{piece.slug}", error=str(exc), agent=agent))
    return out
