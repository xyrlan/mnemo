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

    issues = list(getattr(args, "issues", []) or [])
    contract_path = getattr(args, "contract", None)
    model = getattr(args, "model", None) or None

    # Before the git check, not after: printing the format spawns nothing, and
    # it is the one thing someone runs *before* they have a repo to dispatch
    # from. Refusing it for want of a git root would be refusing documentation.
    if getattr(args, "example", False):
        return _print_example(contract_path)

    root = _repo_root()
    if root is None:
        print("not inside a git repository — dispatch needs one to branch from")
        return 1

    if contract_path == "":
        # `--contract` with nargs="?" and no value. argparse cannot tell the
        # maintainer what is missing; this can.
        print("--contract needs a PATH, or --example to print the format")
        return 1

    if issues and contract_path:
        # Both name the children, and they name different ones. Guessing which
        # the maintainer meant would spawn trees nobody asked for.
        print("pass issue numbers or --contract, not both")
        return 1
    if not issues and not contract_path:
        print("nothing to dispatch: give an issue number or --contract PATH")
        return 1

    lean = not getattr(args, "full_profile", False)

    if contract_path:
        return _dispatch_contract(contract_path, root=root, args=args, lean=lean)

    if getattr(args, "dry_run", False):
        # Printable without side effects: the paths and branches are pure
        # functions of the issue numbers, so the plan can be checked first.
        for issue in issues:
            tree = core.worktree_path(issue, repo_root=root)
            print(f"#{issue}  {core.branch_name(issue)}  {tree}{_model_suffix(model)}")
        return 0

    return _report(
        core.dispatch_all(issues, repo_root=root, model=model, lean=lean),
        lean=lean,
    )


def _print_example(contract_path: str | None) -> int:
    """Emit the canonical example contract on stdout, and nothing else.

    Printed verbatim and alone so the output is a usable file:

        mnemo dispatch --contract --example > docs/contract.md

    A banner or a trailing hint would have to be deleted by hand before the
    file parsed, which is the same friction this closes. The hints go to
    stderr, where a redirect leaves them on the terminal.
    """
    import sys

    from mnemo.core import contracts

    if contract_path:
        # `--contract some/path --example` names both a file to read and a
        # format to print. Refusing beats silently ignoring one of them.
        print("--example prints the format; it does not take a PATH")
        return 1

    print(contracts.EXAMPLE, end="")
    print(
        f"\nWritten by the decomposing-for-dispatch skill ({contracts.SKILL}).\n"
        "Redirect this to a file, edit it, then:\n"
        "    mnemo dispatch --contract <file> --dry-run",
        file=sys.stderr,
    )
    return 0


def _model_suffix(model: str | None) -> str:
    """``"  [haiku]"``, or nothing when no model was chosen (#268).

    Nothing rather than ``[default]``: a dispatch that names no model is the
    unchanged case, and a word in the column would make every line claim a
    choice nobody made. The resolved id is not invented here either — it lives
    on the machine's settings, and ``mnemo sessions`` reads it back off the
    child's own ``state.json`` once the child exists.

    Only the model: the profile is a property of the whole dispatch, not of
    one child, so it is reported once in the footer (#270) rather than
    repeated per row.
    """
    return f"  [{model}]" if model else ""


def _dispatch_contract(
    path: str, *, root: Path, args: argparse.Namespace, lean: bool = True
) -> int:
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

    model = getattr(args, "model", None) or None

    if getattr(args, "dry_run", False):
        for piece in contract.pieces:
            target = f"c-{piece.slug}"
            tree = core.worktree_path(target, repo_root=root)
            branch = core.branch_name(target, feature=contract.feature)
            # The piece's own model wins, exactly as it will at spawn — so a
            # dry run shows what would actually be spent per child, which is
            # the one question the flag creates.
            print(f"{piece.slug}  {branch}  {tree}"
                  f"{_model_suffix(piece.model or model)}")
        return 0

    try:
        results = core.dispatch_contract(
            contract, repo_root=root, model=model, lean=lean
        )
    except core.DispatchError as exc:
        print(str(exc))
        return 1
    return _report(results, lean=lean)


def _label(target: object) -> str:
    """How a child is named in output: ``#197`` for an issue, ``c-parser`` not.

    The ``#`` is not decoration — it is the sigil that makes a bare integer
    read as an issue. A piece slug carrying one would read as an issue number
    that does not exist, which is the same reasoning as ``Session.label``.
    """
    return f"#{target}" if isinstance(target, int) else str(target)


def _report(results: list, *, lean: bool = True) -> int:
    """Print every outcome, then the two commands the maintainer needs next.

    *lean* is printed rather than assumed, because it changes what the child
    can see — a child missing a plugin it needed is a confusing failure to
    debug from the outside, and one line here names the cause (#270).
    """
    started = [r for r in results if r.error is None]
    failed = [r for r in results if r.error is not None]

    for r in started:
        print(f"{_label(r.issue)}  {r.short_id or '????????'}  {r.worktree}"
              f"{_model_suffix(getattr(r, 'model', None))}")
        if r.warning:
            # The child is running — the tree was kept for it — but something
            # about the `claude` CLI did not look as expected (#235). Printed
            # under the row, not swallowed into a blank column: which
            # assumption broke and against which version is the whole point.
            print(f"    WARNING: {r.warning}")
    for r in failed:
        # Never silent: a skipped child the maintainer does not see is one
        # they will assume is running.
        print(f"{_label(r.issue)}  FAILED: {r.error}")

    if started:
        print()
        if lean:
            from mnemo.core import child_profile

            # What the child does *not* have is the surprising half, so it is
            # stated once per dispatch rather than left to be discovered when
            # a child cannot find a tool the maintainer takes for granted.
            print("  profile: lean — repo settings + mnemo only, no user "
                  "plugins/MCP/skills (--full-profile opts out)")
            # Deliberately not the word WARNING, which in this report means
            # "a `claude` CLI assumption broke" (#235). This is a different
            # thing — mnemo is not installed — and giving both the same label
            # would make a report about the machine read as a report about the
            # spawn.
            for gap in child_profile.missing_pieces():
                print(f"    incomplete: {gap}")
        else:
            print("  profile: full user profile (--full-profile)")
        print("  queue:  mnemo sessions")
        # Only a child whose id was actually read back can be attached. When
        # none was, the hint is omitted rather than printed with an empty
        # argument: `claude attach ` reads as actionable and is not, which is
        # the #211 failure in a quieter form. The queue above still finds
        # every child, because it reads `state.json` and not this value.
        attachable = next((r for r in started if r.short_id), None)
        if attachable is not None:
            print(f"  attach: claude attach {attachable.short_id}")
        if any(r.warning for r in started):
            print("  check:  pytest -m live_claude   # the claude CLI contract, "
                  "against the installed binary")

    return 1 if failed else 0
