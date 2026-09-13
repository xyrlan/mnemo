"""``mnemo dispatch`` — one background child per unit of work, one tree each.

The unit is either a GitHub issue (``mnemo dispatch 197 198``) or a piece of a
decomposition contract (``mnemo dispatch --contract plan.md``). They are the
same command because they are the same act: the contract only changes what
names the children and what each one is told.

Human-only, like ``mnemo sessions``: no hook and no MCP tool exposes it.
Spawning work is a decision, and a decision belongs to the maintainer.

This command starts work and stops there. A blocked child *can* be answered
programmatically — ``--resume`` bifurcates, but ``SendMessage`` reaches one —
and it deliberately does not: a child blocks exactly when it wants human
judgement, and an invented answer is the #187 failure again. So the closing
hint points the maintainer at the queue, ``mnemo sessions``, which is
pipe-safe, rather than at ``claude agents``, which requires a TTY.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.parser import command


def _repo_root() -> Path | None:
    """The git toplevel of the cwd, or ``None`` when there is not one."""
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


@command("dispatch")
def cmd_dispatch(args: argparse.Namespace) -> int:
    """Spawn a child per issue. Returns 1 if any of them failed to start."""
    from mnemo.core import dispatch as core

    root = _repo_root()
    if root is None:
        print("not inside a git repository — dispatch needs one to branch from")
        return 1

    issues = list(getattr(args, "issues", []) or [])
    contract_path = getattr(args, "contract", None)

    if issues and contract_path:
        # Both name the children, and they name different ones. Guessing which
        # the maintainer meant would spawn trees nobody asked for.
        print("pass issue numbers or --contract, not both")
        return 1
    if not issues and not contract_path:
        print("nothing to dispatch: give an issue number or --contract PATH")
        return 1

    if contract_path:
        return _dispatch_contract(contract_path, root=root, args=args)

    if getattr(args, "dry_run", False):
        # Printable without side effects: the paths and branches are pure
        # functions of the issue numbers, so the plan can be checked first.
        for issue in issues:
            tree = core.worktree_path(issue, repo_root=root)
            print(f"#{issue}  {core.branch_name(issue)}  {tree}")
        return 0

    return _report(core.dispatch_all(issues, repo_root=root))


def _dispatch_contract(path: str, *, root: Path, args: argparse.Namespace) -> int:
    """Read, validate, then dispatch — refusing before any tree is created."""
    from mnemo.core import contracts
    from mnemo.core import dispatch as core

    try:
        contract = contracts.parse_contract(path)
    except contracts.ContractError as exc:
        # A contract that cannot be trusted is a decomposition to redo, not a
        # dispatch to retry, so the message names the file rather than a step.
        # The path comes from the argument, not from the parsed contract: the
        # exception is precisely what stopped one from existing.
        print(f"contract unusable: {path}: {exc}")
        return 1

    if getattr(args, "dry_run", False):
        for piece in contract.pieces:
            target = f"c-{piece.slug}"
            tree = core.worktree_path(target, repo_root=root)
            branch = core.branch_name(target, feature=contract.feature)
            print(f"{piece.slug}  {branch}  {tree}")
        return 0

    try:
        results = core.dispatch_contract(contract, repo_root=root)
    except core.DispatchError as exc:
        print(str(exc))
        return 1
    return _report(results)


def _label(target: object) -> str:
    """How a child is named in output: ``#197`` for an issue, ``c-parser`` not.

    The ``#`` is not decoration — it is the sigil that makes a bare integer
    read as an issue. A piece slug carrying one would read as an issue number
    that does not exist, which is the same reasoning as ``Session.label``.
    """
    return f"#{target}" if isinstance(target, int) else str(target)


def _report(results: list) -> int:
    """Print every outcome, then the two commands the maintainer needs next."""
    started = [r for r in results if r.error is None]
    failed = [r for r in results if r.error is not None]

    for r in started:
        print(f"{_label(r.issue)}  {r.short_id}  {r.worktree}")
    for r in failed:
        # Never silent: a skipped child the maintainer does not see is one
        # they will assume is running.
        print(f"{_label(r.issue)}  FAILED: {r.error}")

    if started:
        print()
        print("  queue:  mnemo sessions")
        # Only a child whose id was actually read back can be attached. When
        # none was, the hint is omitted rather than printed with an empty
        # argument: `claude attach ` reads as actionable and is not, which is
        # the #211 failure in a quieter form. The queue above still finds
        # every child, because it reads `state.json` and not this value.
        attachable = next((r for r in started if r.short_id), None)
        if attachable is not None:
            print(f"  attach: claude attach {attachable.short_id}")

    return 1 if failed else 0
