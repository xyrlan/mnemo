"""``mnemo resume`` — put back to work the children a clock has freed (#393).

Six children were dispatched on 2026-09-19 with the account's five-hour window
nearly spent. Each ran about eight minutes and stopped on ``You've hit your
session limit``. Six hours later the queue had all six under **ABANDONED**
next to ``remove: claude rm <id>`` — the hint that deletes the worktree — and
five of the six trees held uncommitted work. Nothing was dead. Woken by hand,
two commands each, all six finished with a PR.

This is that recovery as one command for all of them.

**What it will and will not wake.**

- Only a session the roster proves dead. ``--resume`` on a *running* one
  starts a copy (``resume-bifurcates``), and a copy in a child's worktree is
  the accident one-worktree-per-child exists to prevent.
- Only a stall a clock frees: the account's window, or an API that asked for
  a retry (:mod:`mnemo.core.sessions.stalls`). A child blocked on a question,
  a permission prompt, a login or a billing decision is *not* woken — waking
  it replays the same failing turn, and what it needs is an answer. Those are
  listed with ``claude attach``, which is where an answer goes.
- Only past the reset. The epoch comes from the child's own transcript
  (``quotaLimits.resetsAt``), so this is the child's evidence, not a guess.

**What bounds re-spending the limit.** The reset does, and it is the only
thing that can: no ``claude`` subcommand, flag or file reports how much of the
window is left — ``/usage`` is an in-session slash command and the usage
endpoint is not on the CLI. So the honest statement, which this command
prints rather than implies: past its reset the window is *fresh*, which is
why waking all of them at once was safe on 2026-09-19 and why the count is
not the thing being bounded. N children do not spend more of a window than
one child doing the same work — they spend it N times faster. A maintainer
who wants it spread out names ids instead, and ``--dry-run`` shows what a
bare ``mnemo resume`` would do without spending a token.

**Naming an id is not the approval here**, and that is the difference from
``mnemo deliver``. Deliver publishes: it takes a decision per child that
cannot be taken back, which is why it has no ``--all``. Waking publishes
nothing, grants nothing and changes nothing about the task — the child
carries on with the prompt the maintainer already wrote. The decision being
taken N times is "continue the work I already dispatched", and taking it once
is the whole point of the issue's "one command, or none".

*Or none* was considered and refused. mnemo has no daemon on purpose
(:mod:`mnemo.core.sessions.detector` says why), and the triggers a sweep
could ride — ``mnemo sessions``, the ``SessionEnd`` hook — are read-only
today. Spending the account's budget as a side effect of printing a queue is
not a thing a queue should do.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from mnemo.cli.parser import command


def _repo_root() -> Optional[Path]:
    """The git toplevel of the cwd, or ``None``. As ``deliver`` reads it."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return Path(top) if top else None


def _matches(session, wanted: set) -> bool:
    """True when *session* is one of the ids or issues the maintainer named.

    Three spellings, because those are the three a maintainer has in front of
    them: the short id the queue prints, a unique prefix of it, and the issue
    number — which is what they actually remember, and which the worktree path
    already encodes (``dispatch.issue_for_cwd``).
    """
    from mnemo.core.dispatch import issue_for_cwd

    short_id = getattr(session, "short_id", "") or ""
    target = issue_for_cwd(getattr(session, "cwd", None))
    for token in wanted:
        if short_id and short_id.startswith(token):
            return True
        if target is not None and token.lstrip("#") == str(target):
            return True
    return False


def _describe(session, stall) -> str:
    """One line naming the child and what stopped it."""
    from mnemo.core.sessions.render import _stall_line

    return f"{session.short_id}  {session.label}  —  {_stall_line(stall)}"


@command("resume")
def cmd_resume(args: argparse.Namespace) -> int:
    """Wake every stalled child whose reset has passed."""
    import os

    from mnemo.core.sessions import stalls as stalls_mod
    from mnemo.core.sessions import wake as wake_mod
    from mnemo.core.sessions.jobs import normalize_cwd, read_sessions

    scope = None if getattr(args, "all", False) else normalize_cwd(os.getcwd())
    named = {str(x).strip() for x in (getattr(args, "ids", None) or []) if str(x).strip()}

    found = [s for s in read_sessions(cwd=scope) if s.is_abandoned]
    if named:
        found = [s for s in found if _matches(s, named)]

    stalls = stalls_mod.stalls_for(found)

    free, waiting, human = [], [], []
    for session in found:
        stall = stalls.get(session.short_id)
        if stall is None or not stall.clock_freed:
            human.append((session, stall))
        elif stall.is_free():
            free.append((session, stall))
        else:
            waiting.append((session, stall))

    for session, stall in waiting:
        seconds = stall.free_in() or 0
        print(f"  waiting: {_describe(session, stall)} "
              f"(~{max(1, seconds // 60)}m from now)")
    for session, stall in human:
        # Not a failure and not hidden: these are exactly the sessions the
        # issue asked to keep separate. A nudge is the wrong answer for them.
        print(f"  needs you: {session.short_id}  {session.label}  —  "
              f"{session.needs or session.detail or 'blocked'}  "
              f"(claude attach {session.short_id})")

    if not free:
        if not found:
            if scope is None:
                print("no stalled session to resume, in any repo")
            else:
                print("no stalled session in this repo to resume "
                      "(mnemo resume --all looks in every repo)")
        elif not (waiting or human):
            print("nothing to resume")
        return 0

    if getattr(args, "dry_run", False):
        for session, stall in free:
            print(f"  would resume: {_describe(session, stall)}")
        print(f"{len(free)} session(s) would be woken; nothing was spent")
        return 0

    woken, failed = 0, 0
    for session, stall in free:
        cwd = session.cwd or os.getcwd()
        if not os.path.isdir(cwd):
            # The tree is gone, so there is nothing left to carry on with —
            # and `--resume` would wake the session into a directory that does
            # not exist. Reported, not silently skipped.
            print(f"  skipped: {session.short_id}  {session.label}  —  "
                  f"worktree {cwd} is gone")
            failed += 1
            continue
        why = wake_mod.wake(session.session_id or "", cwd=cwd)
        if why:
            print(f"  failed: {session.short_id}  {session.label}  —  {why}")
            failed += 1
            continue
        woken += 1
        print(f"  resumed: {_describe(session, stall)}")

    print(f"{woken} resumed, {failed} failed"
          if failed else f"{woken} resumed")
    if woken:
        # The one thing a maintainer cannot read anywhere else, and the reason
        # the count above is not a budget: see the module docstring.
        print("  they share one account window; mnemo cannot see how much of "
              "it is left, only that the reset has passed")
    return 0
