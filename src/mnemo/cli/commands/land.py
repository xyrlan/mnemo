"""``mnemo land`` — the last metre of a *contract*: land its pieces in order.

``mnemo deliver`` ends with each piece pushed and its PR open. What is left is
the part a contract exists for: piece A ``consumes`` a signature piece B
``exposes``, A was written against a signature that did not exist yet, and
the merge is where it becomes real. Until #236 that resolution was done by
hand — merge in dependency order, run the suite after each, find out at the
end whether A's assumption about B held.

Two shapes, mirroring ``deliver``:

- ``mnemo land <contract.md>`` is read-only. Every piece in landing order,
  its PR and state, the ref that carries it, and whether each ``exposes`` is
  defined in the piece's files on that ref. Prints and exits.
- ``mnemo land <contract.md> --merge`` finishes it. First a **rehearsal** in
  a throwaway worktree — merge each piece in order, check its signatures in
  the merged tree, run the suite — which stops at the first conflict,
  missing signature or red suite with nothing changed anywhere. Only when
  that passed in full are the PRs merged with ``gh``, in the same order.

A new verb rather than a mode of ``deliver``, because ``deliver``'s invariant
is that *naming an id is the approval* and there is no flag that approves N
children. A contract landing is inherently every piece of the contract; the
per-piece approval already happened when each was delivered, and what is
left is sequencing. Putting that under ``deliver`` would make its docstring
false.

Not a scheduler. Dispatch stays a flat fan-out; this runs after every piece
landed as a PR, and it is sequential by nature.
"""
from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from mnemo.cli.parser import command

# The suite when `--suite` is not given. `sys.executable -m pytest` rather
# than a bare `pytest`, so the interpreter that runs mnemo is the one that
# runs the suite. `landing.rehearse` prepends the rehearsal tree's `src` to
# PYTHONPATH when there is one, so a src-layout repo tests the merged tree
# and not whatever editable install the interpreter carries.
DEFAULT_SUITE = f"{shlex.quote(sys.executable)} -m pytest -q"


def _repo_root() -> Path | None:
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


def _mark(ok: bool | None) -> str:
    """``✓`` present, ``✗`` missing, ``?`` unverifiable (no identifier)."""
    return "?" if ok is None else ("✓" if ok else "✗")


def _print_view(states, *, contract_path: str) -> None:
    """One block per piece, in landing order. The whole read-only report."""
    width = max(len(s.piece.slug) for s in states)
    for index, s in enumerate(states, start=1):
        where = s.ref or "—"
        print(f"  {index}. {s.piece.slug:<{width}}  {s.branch}  [{where}]")
        if s.pr:
            print(f"       PR: {s.pr} ({s.pr_state})")
        else:
            print("       PR: nenhum")
        if s.exposes:
            for sig, ok in s.exposes:
                print(f"       expõe   {_mark(ok)} {sig}")
        if s.consumes:
            for sig, owner, ok in s.consumes:
                print(f"       consome {_mark(ok)} {sig} de {owner}")
        if s.reason:
            print(f"       ✗ {s.reason}")

    blocked = [s for s in states if not s.landable]
    print()
    if blocked:
        print(f"NÃO POUSA ({len(blocked)}): "
              + ", ".join(s.piece.slug for s in blocked))
    else:
        pending = [s for s in states if not s.merged]
        if pending:
            print(f"  pousar: mnemo land {contract_path} --merge")
        else:
            print("  tudo pousado — nada a fazer")


@command("land")
def cmd_land(args: argparse.Namespace) -> int:
    """Show a contract's landing order and signatures, or land it."""
    from mnemo.core import contracts, landing

    root = _repo_root()
    if root is None:
        print("not inside a git repository — land reads the pieces' branches")
        return 1

    path = str(getattr(args, "contract", "") or "")
    try:
        contract = contracts.parse_contract(path)
    except contracts.ContractError as exc:
        # The path from the argument, not the contract: the exception is what
        # stopped one from existing. Same shape as `dispatch --contract`.
        print(f"contract unusable: {path}: {exc}")
        return 1

    try:
        states = landing.inspect(contract, repo_root=root)
    except landing.LandingError as exc:
        print(str(exc))
        return 1

    print(f"contrato {contract.feature} ({len(states)} peças, em ordem de pouso)")
    _print_view(states, contract_path=path)

    blocked = [s for s in states if not s.landable]
    if blocked:
        return 1
    if not getattr(args, "merge", False):
        return 0

    return _land(states, root=root, args=args, contract_path=path)


def _land(states, *, root: Path, args: argparse.Namespace, contract_path: str) -> int:
    """Rehearse, and merge only after the rehearsal passed in full."""
    from mnemo.core import landing

    if all(s.merged for s in states):
        return 0

    suite = shlex.split(str(getattr(args, "suite", None) or DEFAULT_SUITE))
    method = str(getattr(args, "method", None) or "squash")

    print()
    print("ENSAIO (worktree temporário, nada é pousado ainda)")
    try:
        rehearsal = landing.rehearse(states, repo_root=root, suite=suite)
    except landing.LandingError as exc:
        print(f"  {exc}")
        return 1
    for step in rehearsal.steps:
        mark = "·" if step.skipped else ("✓" if step.ok else "✗")
        head, _, rest = step.detail.partition("\n")
        print(f"  {mark} {step.slug}: {head}")
        for line in rest.splitlines():
            print(f"      {line}")
    if not rehearsal.ok:
        failed = rehearsal.failed
        print()
        print(f"parou em {failed.slug}: nada foi pousado. Corrija a peça e "
              f"rode `mnemo land {contract_path} --merge` de novo.")
        return 1

    print()
    print("POUSO")
    steps = landing.merge_prs(states, repo_root=root, method=method)
    for step in steps:
        mark = "·" if step.skipped else ("✓" if step.ok else "✗")
        print(f"  {mark} {step.slug}: {step.detail}")
    failed = next((s for s in steps if not s.ok), None)
    if failed is not None:
        landed = [s.slug for s in steps if s.ok and not s.skipped]
        print()
        print(f"parou em {failed.slug}"
              + (f" — pousadas: {', '.join(landed)}" if landed else "")
              + f". As que pousaram são puladas ao rodar "
              f"`mnemo land {contract_path} --merge` de novo.")
        return 1
    print()
    print("  contrato pousado")
    return 0
