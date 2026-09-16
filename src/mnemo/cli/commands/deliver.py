"""``mnemo deliver`` — the last metre of a dispatch, from the parent's side.

Two commands, not a prompt:

- ``mnemo deliver --review`` is read-only. Every dispatch worktree, whether it
  is clean, how far ahead of the base branch, a diffstat, and any PR that already
  exists. Prints and exits, touching nothing.
- ``mnemo deliver <id> [<id>...]`` pushes and opens a PR for **exactly** the
  ids named, and nothing else. Once a PR is open it stops the child that did
  the work, if that child is ``done`` (#311) — never one still working.

**Naming an id is the approval.** There is deliberately no ``--all`` and no
"deliver everything that is ready": one flag approving N children is precisely
the failure mode this exists to prevent (#215). The maintainer reviews each
diff — the expensive part, and the part that must stay per-child — and then
spends one command instead of three on the mechanics, which are not worth
repeating N times.

Both halves work on a pipe. ``mnemo sessions`` and ``mnemo session`` are
pipe-safe by design and this joins them; an interactive y/n confirmation would
break that, and was rejected for it. The approval is in the argv, where it is
visible in shell history, not in a keystroke nobody can audit.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.parser import command


def _repo_root() -> Path | None:
    """The git toplevel of the cwd, or ``None``. As ``dispatch`` reads it."""
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


def _review(*, repo_root: Path) -> int:
    """Print every dispatch worktree and what it would take to deliver it.

    Read-only, and says so by doing nothing else. Returns 0 even when nothing
    is ready: "nothing to deliver" is a successful report, not a failure —
    this is the command a maintainer runs to find out.
    """
    from mnemo.core.sessions import delivery

    trees = delivery.dispatch_worktrees(repo_root=repo_root)
    if not trees:
        print("no dispatch worktrees — nothing to deliver")
        return 0

    # Resolved once: every tree shares the repo's refs, and the resolution can
    # cost a `gh` call on a repo with no `origin/HEAD`.
    base = delivery.base_branch(repo_root=repo_root)
    states = [delivery.ready(tree, repo_root=repo_root, base=base) for tree in trees]
    ready = [r for r in states if r.ready]
    blocked = [r for r in states if not r.ready]

    if ready:
        print(f"PRONTAS ({len(ready)})")
        for r in ready:
            ahead = f"{r.ahead} commit" + ("s" if r.ahead != 1 else "")
            print(f"  {r.label}  {r.branch}")
            print(f"      {ahead} ahead of {r.base}"
                  + (f", {r.diffstat}" if r.diffstat else ""))
            if r.pr:
                # Named, not filtered out. An existing PR usually means this
                # was already delivered — but a branch pushed again after
                # review comments is the same row, and only the maintainer
                # knows which one this is.
                print(f"      PR já existe: {r.pr}")
        print()

    if blocked:
        print(f"NÃO PRONTAS ({len(blocked)})")
        for r in blocked:
            print(f"  {r.label}  {r.branch or '—'}")
            print(f"      {r.reason}")
        print()

    # A finished child still running holds a few hundred MB and has written no
    # briefing, whether or not it had anything to deliver. Said once, across
    # both groups: the child with nothing to deliver is the one most worth
    # stopping, because no diff carries what it decided.
    holding = [
        (r.label, s.short_id)
        for r in states
        for s in delivery.sessions_in(r.worktree)
        if s.state == "done" and s.live is not False
    ]
    if holding:
        print(f"TERMINADAS, NÃO PARADAS ({len(holding)})")
        for label, short_id in holding:
            print(f"  {label}  {short_id}")
        print("      sem briefing até parar: mnemo deliver --stop-done")
        print()

    if ready:
        names = " ".join(r.label.lstrip("#") for r in ready)
        # The exact command, with the ids spelled out. Naming them is the
        # approval, so the hint that saves the typing must not collapse them
        # into a flag — copying this line is still a per-child decision the
        # maintainer can edit before running.
        print(f"  entregar: mnemo deliver {names}")
    return 0


def _deliver_one(named: str, *, repo_root: Path) -> bool:
    """Push and open a PR for one named id. True when it was delivered.

    Every refusal is printed against the name the maintainer typed, so a list
    of four reads as four outcomes rather than one aggregate.
    """
    from mnemo.core.sessions import delivery

    tree = delivery.find_worktree(named, repo_root=repo_root)
    if tree is None:
        print(f"{named}: no dispatch worktree — "
              f"run `mnemo deliver --review` to see what there is")
        return False

    state = delivery.ready(tree, repo_root=repo_root)
    if not state.ready:
        # Refused with the reason, never pushed anyway.
        print(f"{state.label}: {state.reason}")
        return False

    if state.pr and state.pr_state == "OPEN":
        # Not a push. Delivering twice would open a duplicate PR for the same
        # branch, and the maintainer who wants the existing one updated can
        # push it themselves — that is a different decision from the one this
        # command takes, and it is already reviewed.
        #
        # Not an error either, and since #317 the expected outcome: a child
        # dispatched with `--may pr` opened this PR itself. So it counts as
        # delivered — the closing trailer the child may have left off is
        # added, and the finished child is stopped, exactly as after a PR this
        # command opened.
        #
        # Only an OPEN one. A MERGED or CLOSED PR on this branch name is a
        # previous life of the name — `fix/issue-158` was dispatched twice,
        # two days apart — and the commits ahead of the base now are new work
        # that `gh pr create` will open a new PR for.
        print(f"{state.label}: PR já existe — {state.pr}")
        delivery.close_on_merge(state.pr, target=state.target, worktree=tree)
        _stop_finished(tree, label=state.label)
        return True

    try:
        delivery.push(state.branch, worktree=tree)
    except delivery.DeliveryError as exc:
        print(f"{state.label}: push failed: {exc}")
        return False

    title = f"{state.label}: {state.branch}"
    try:
        url = delivery.open_pr(
            state.branch, worktree=tree, title=title, target=state.target,
        )
    except delivery.DeliveryError as exc:
        # The push landed. Saying so matters: the branch is on the remote and
        # a retry must not read as though nothing happened.
        print(f"{state.label}: pushed {state.branch}, but gh pr create failed: {exc}")
        return False

    print(f"{state.label}: {url or state.branch + ' pushed, PR created'}")
    _stop_finished(tree, label=state.label)
    return True


def _stop_finished(tree: Path, *, label: str) -> None:
    """Stop the child that did this work, now that its PR is open (#311).

    Nothing else stops it. The daemon retires a finished child after 8 h idle,
    and until then it holds ~300-400 MB (three measured 2026-09-15); worse,
    only a *stopped* child fires ``SessionEnd`` (#247), so a child left to the
    daemon never writes its briefing. The worktree is untouched — SessionEnd
    resolves its cwd, so the stop has to come before any removal, and this
    command removes nothing.

    Only ``state == "done"``. A child still working, or blocked on a question,
    has not finished and is not this command's to end: it gets one line saying
    so. ``stopped`` is already stopped. A ``done`` session the roster proves is
    gone (``live is False``) has no process left to end.
    """
    from mnemo.core.sessions import delivery

    for session in delivery.sessions_in(tree):
        if session.state == "stopped" or (
            session.state == "done" and session.live is False
        ):
            continue
        if session.state != "done":
            print(f"{label}: {session.short_id} left running "
                  f"(state={session.state or '?'})")
            continue
        why = delivery.stop_session(session.short_id)
        if why is None:
            print(f"{label}: stopped {session.short_id}")
        else:
            print(f"{label}: `claude stop {session.short_id}` failed: {why}")


def _stop_done(*, repo_root: Path) -> int:
    """Stop every finished child in every dispatch worktree of this repo.

    The wider net around :func:`_stop_finished`, which only ever runs after a
    *successful* delivery. A child that judged the task wrong and declined has
    no commits, so it delivers nothing — and is therefore exactly the child
    that delivery-shaped stopping can never reach. Its briefing is the only
    copy of the reasoning, because there is no diff to read it off.

    Pushes nothing, opens nothing, removes nothing: the whole command is the
    ``claude stop`` that makes ``SessionEnd`` fire (#247). Returns 0 even when
    nothing was stopped — "nothing finished and still running" is a successful
    report, the same way ``--review`` finding nothing ready is.
    """
    from mnemo.core.sessions import delivery

    stopped = 0
    for tree in delivery.dispatch_worktrees(repo_root=repo_root):
        for short_id, why in delivery.stop_done_in(tree):
            if why is None:
                print(f"{tree.name}: stopped {short_id}")
                stopped += 1
            else:
                print(f"{tree.name}: `claude stop {short_id}` failed: {why}")
    if not stopped:
        print("nothing finished and still running")
    return 0


@command("deliver")
def cmd_deliver(args: argparse.Namespace) -> int:
    """Review what is deliverable, or deliver exactly the ids named."""
    root = _repo_root()
    if root is None:
        print("not inside a git repository — deliver reads the dispatch worktrees")
        return 1

    ids = list(getattr(args, "ids", []) or [])
    review = bool(getattr(args, "review", False))

    if getattr(args, "stop_done", False):
        # Before the id handling, and instead of it: this delivers nothing,
        # so the "nothing named" refusal below must not catch it. Named ids
        # and --review are refused rather than ignored — each means a
        # different command, and silently dropping one would report on a
        # sweep the maintainer did not ask for.
        if ids or review:
            print("--stop-done stops finished children and delivers nothing — "
                  "run it on its own")
            return 1
        return _stop_done(repo_root=root)

    if review and ids:
        # --review is read-only and delivering is not. Running both would make
        # the report a preamble to a push the maintainer may have meant to
        # read first.
        print("pass --review or ids to deliver, not both")
        return 1
    if not review and not ids:
        print("nothing named: `mnemo deliver --review` to see what is ready, "
              "then `mnemo deliver <id> [<id>...]`")
        return 1

    if review:
        return _review(repo_root=root)

    delivered = [_deliver_one(named, repo_root=root) for named in ids]
    return 0 if all(delivered) else 1
