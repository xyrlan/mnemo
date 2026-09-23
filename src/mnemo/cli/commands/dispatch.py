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

#: Printed last, only when dispatch runs inside a Claude Code session (#306).
#: It states the fact first — nothing wakes the dispatching session — because
#: the misreport it prevents is a promise with no mechanism behind it. The
#: carve-out is deliberate: a maintainer who asks the session to watch the
#: children can have it arm something that does wake it, and a flat "never
#: poll" would contradict the one honest way to keep that promise. It does not
#: repeat `mnemo sessions`: the queue line already names it, and a note that
#: said it again would only give the model one more copy to hand back.
AGENT_NOTE = (
    "  note to the session that ran this: the children run detached, and nothing",
    "  tells this session when they block or finish. Report the ids above and stop;",
    "  do not poll them or promise to watch or report back unless the maintainer",
    "  asked you to. The queue and attach lines above are for the maintainer.",
)

#: The same note when ``dispatch.notifyParent`` is on, the default (#426). The
#: finish notice is a mechanism, so the note can name it — but only for what
#: it does: it reaches this session while it is open, it says the child
#: finished, and it does not say when one blocks. "No need to poll" is the
#: point: the polling it replaces cost a lookup after 56 of 58 notices.
AGENT_NOTE_NOTIFIED = (
    "  note to the session that ran this: the children run detached. When one exits,",
    "  mnemo posts a notice into this session while it is still open: the child's PR,",
    "  its checks and its closing report, and once more when running checks settle.",
    "  Nothing tells this session when a child blocks. Report the ids above and stop;",
    "  there is no need to poll. The queue and attach lines above are for the maintainer.",
)


def _agent_note() -> tuple:
    """The note that matches what this machine will actually do."""
    try:
        from mnemo.core import config as cfg_mod

        cfg = cfg_mod.load_config()
    except Exception:  # noqa: BLE001 — the note must never fail a dispatch
        return AGENT_NOTE
    notify = bool((cfg.get("dispatch") or {}).get("notifyParent", False))
    return AGENT_NOTE_NOTIFIED if notify else AGENT_NOTE


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


def _default_grant(value: str | None) -> grants.Grant:
    """What a child may publish, defaulting to ``pr`` when nothing was said.

    The default inverted on 2026-09-16. It used to be ``()`` — "Do not merge
    or push without asking" — which left the child finished, unpublished and
    running, and a child that never stops never writes a briefing. Every grant
    ever recorded in the vault was ``push`` or ``push,pr``, so this matches
    what dispatch was already used for rather than changing it.

    ``--may none`` is untouched and still withholds: the opt-out has to
    survive the default, or a spike that should not become a branch has
    nowhere to go.

    This is the command line's decision, not the library's: every core entry
    point keeps ``may: Grant = ()``, so a caller that says nothing still gets
    nothing.
    """
    from mnemo.core.sessions import grants

    if value is None:
        return grants.parse("pr")
    return grants.parse(value)


def _grant_for(args: argparse.Namespace) -> grants.Grant:
    """The grant this dispatch runs with, `()` when the posture is read-only.

    Keyed on what was *typed*: `--may` defaults to `pr`, so resolving the grant
    first and then refusing a non-empty one would make `--read-only` unusable
    on its own.
    """
    from mnemo.core.sessions import grants

    if getattr(args, "read_only", False):
        return ()
    return _default_grant(getattr(args, "may", None))


@command("dispatch")
def cmd_dispatch(args: argparse.Namespace) -> int:
    """Spawn a child per issue. Returns 1 if any of them failed to start."""
    from mnemo.core import dispatch as core

    issues = list(getattr(args, "issues", []) or [])
    contract_path = getattr(args, "contract", None)
    model = getattr(args, "model", None) or None
    effort = getattr(args, "effort", None) or None

    from mnemo.core import claude_cli
    from mnemo.core.sessions import grants

    if effort is not None and effort not in claude_cli.EFFORT_LEVELS:
        # argparse's `choices` already refuses this on the command line; this
        # is for a namespace built by hand. Before anything could spawn (#351).
        print(f"--effort: unknown level {effort!r}: "
              f"use one of {', '.join(claude_cli.EFFORT_LEVELS)}")
        return 1

    read_only = bool(getattr(args, "read_only", False))
    if read_only and getattr(args, "may", None) is not None:
        # Before anything could spawn, as the grant refusal is. Not a
        # tightening or a loosening but a contradiction: a read-only child
        # produces nothing to publish, so a publishing grant on it is a
        # statement about work that cannot exist.
        print("--read-only: a read-only child publishes nothing, "
              "so it cannot be given --may")
        return 1
    try:
        may = _grant_for(args)
    except grants.GrantError as exc:
        # Before anything else that could spawn: a grant that cannot be given
        # is refused while there is still nothing to roll back.
        print(f"--may: {exc}")
        return 1

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

    if getattr(args, "twins", False):
        return _dispatch_twins(
            issues, contract_path=contract_path, root=root, args=args, lean=lean,
            model=model, effort=effort,
        )

    if contract_path:
        return _dispatch_contract(
            contract_path, root=root, args=args, lean=lean, may=may,
            read_only=read_only,
        )

    if getattr(args, "dry_run", False):
        # Printable without side effects: the paths and branches are pure
        # functions of the issue numbers, so the plan can be checked first.
        for issue in issues:
            tree = core.worktree_path(issue, repo_root=root)
            print(f"#{issue}  {core.branch_name(issue)}  {tree}"
                  f"{_model_suffix(model, effort)}{_may_suffix(may)}")
        return 0

    return _report(
        core.dispatch_all(
            issues, repo_root=root, model=model, lean=lean, may=may, effort=effort,
            read_only=read_only,
        ),
        lean=lean,
    )


def _dispatch_twins(
    issues: list, *, contract_path: str | None, root: Path,
    args: argparse.Namespace, lean: bool, model: str | None, effort: str | None,
) -> int:
    """One issue as two blind children, for #439's pilot (#449).

    Every refusal is before anything spawns. What twins may publish is not a
    choice: nothing, because the maintainer picks one after reading both
    blind — so a typed ``--may`` that grants anything is refused rather than
    quietly dropped, and ``--read-only`` is refused because a twin builds.
    """
    from mnemo.core import dispatch as core
    from mnemo.core import twins
    from mnemo.core.sessions import grants

    if contract_path:
        print("--twins runs one issue twice; a contract is not an issue")
        return 1
    if len(issues) != 1:
        print(f"--twins runs exactly one issue twice, not {len(issues)}")
        return 1
    if getattr(args, "read_only", False):
        print("--twins: a twin builds; --read-only cannot be its posture")
        return 1
    typed = getattr(args, "may", None)
    if typed is not None and grants.parse(typed):
        print("--twins: twins publish nothing — you deliver the one you prefer "
              "after reading both blind, so --may can only be none")
        return 1

    issue = issues[0]
    if getattr(args, "dry_run", False):
        for _ in twins.LABELS:
            tree = core.worktree_path(issue, repo_root=root).name + "-<tag>"
            print(f"#{issue}  fix/issue-{issue}-<tag>  {root.parent / tree}"
                  f"{_model_suffix(model, effort)}")
        return 0

    try:
        pair_id, results = twins.dispatch_twins(
            issue, repo_root=root, model=model, lean=lean, effort=effort,
        )
    except core.DispatchError as exc:
        print(f"#{issue}  FAILED: {exc}")
        return 1
    code = _report(results, lean=lean)
    started = [r for r in results if r.error is None]
    if len(started) == len(twins.LABELS):
        print(f"  pair:   {pair_id} — once both finish: mnemo twins show {pair_id}")
    elif started:
        print(f"  pair:   {pair_id} — only one twin started; it is not a pair "
              "and the pilot leaves it out")
    return code


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


def _model_suffix(model: str | None, effort: str | None = None) -> str:
    """``"  [haiku]"``, or nothing when no model was chosen (#268).

    An effort, when one was chosen, joins the same tag (#351):
    ``"  [haiku, effort high]"``, or ``"  [effort high]"`` without a model.

    Nothing rather than ``[default]``: a dispatch that names no model is the
    unchanged case, and a word in the column would make every line claim a
    choice nobody made. The resolved id is not invented here either — it lives
    on the machine's settings, and ``mnemo sessions`` reads it back off the
    child's own ``state.json`` once the child exists.

    Only the model: the profile is a property of the whole dispatch, not of
    one child, so it is reported once in the footer (#270) rather than
    repeated per row.
    """
    parts = [p for p in (model, effort and f"effort {effort}") if p]
    return f"  [{', '.join(parts)}]" if parts else ""


def _may_suffix(may: tuple[str, ...]) -> str:
    """``"  may: push+pr"``, or nothing when nothing was granted (#317).

    Nothing rather than ``may: none`` for the reason :func:`_model_suffix`
    gives: the default is the unchanged case, and a word on every row would
    make each one read as a decision.
    """
    return f"  may: {'+'.join(may)}" if may else ""


def _dispatch_contract(
    path: str, *, root: Path, args: argparse.Namespace, lean: bool = True,
    may: tuple[str, ...] = (), read_only: bool = False,
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
    effort = getattr(args, "effort", None) or None

    if getattr(args, "dry_run", False):
        for piece in contract.pieces:
            target = f"c-{piece.slug}"
            tree = core.worktree_path(target, repo_root=root)
            branch = core.branch_name(target, feature=contract.feature)
            # The piece's own model wins, exactly as it will at spawn — so a
            # dry run shows what would actually be spent per child, which is
            # the one question the flag creates.
            print(f"{piece.slug}  {branch}  {tree}"
                  f"{_model_suffix(piece.model or model, piece.effort or effort)}"
                  f"{_may_suffix(core.piece_grant(piece, may))}")
        return 0

    try:
        results = core.dispatch_contract(
            contract, repo_root=root, model=model, lean=lean, may=may,
            effort=effort, read_only=read_only,
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
              f"{_model_suffix(getattr(r, 'model', None), getattr(r, 'effort', None))}"
              f"{_may_suffix(getattr(r, 'may', ()))}")
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
        from mnemo.core import child_profile

        print()
        # What the spawn actually did, not what was asked for: the
        # environment variable opts out too, and a report that consulted only
        # the flag would announce a lean child that started on the full
        # profile.
        if child_profile.is_lean(lean):
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
            # Name which of the two opt-outs is in force, so a maintainer who
            # exported the variable in a previous shell and forgot is not left
            # wondering why --full-profile appears to be on.
            why = ("MNEMO_DISPATCH_FULL_PROFILE" if child_profile.env_opts_out()
                   else "--full-profile")
            print(f"  profile: full user profile ({why})")
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
        from mnemo.core.sessions import parents

        # Inside a session this output is read by the model, not the
        # maintainer, and the `queue:` line above reads as its own next step:
        # in real transcripts it came back as "Acompanho com `mnemo sessions`…
        # te aviso quando terminarem" with nothing armed to notice (#306). A
        # plain terminal has no such reader, so it keeps the footer as it was.
        if parents.parent_from_env() is not None:
            print()
            for line in _agent_note():
                print(line)

    return 1 if failed else 0
