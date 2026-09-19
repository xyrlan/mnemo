"""`mnemo procedures` — what children keep rediscovering, as a CLAUDE.md line (#392).

    mnemo procedures                # candidates for the repo you are standing in
    mnemo procedures --all          # every repo the transcripts on disk cover
    mnemo procedures --show KEY     # the proposed line, and who paid for it
    mnemo procedures --accept KEY   # append that section to the repo's CLAUDE.md
    mnemo procedures --drop KEY     # take it out of the queue, write nothing

``CLAUDE.md`` is the channel #385 measured as reaching every child, whatever
the issue says; a ranked rule reaches the child whose prompt happens to match.
So a *procedure* — how work is run in this repo — belongs in the file, and
:mod:`mnemo.core.procedures` finds the ones children worked out for themselves
more than once. What it cannot see, it says: read that module's docstring
before trusting a listing's silence.

Listing writes nothing. ``--accept`` is the one write, it only ever appends,
and there is no ``--accept-all``: the file is read by every session in that
repo for ever, so each line is worth one decision.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


def _repo_here() -> str | None:
    """The repo the shell is standing in, worktrees folded to their origin."""
    import os

    try:
        from mnemo.core.agent import resolve_canonical_agent

        return resolve_canonical_agent(os.getcwd()).name or None
    except Exception:  # noqa: BLE001 — a listing is better than a traceback
        return None


def _scan(args: argparse.Namespace, vault):
    import os

    from mnemo.core import config as cfg_mod
    from mnemo.core import procedures as P

    cfg = cfg_mod.load_config().get("procedures", {})
    projects = getattr(args, "projects", None) or os.path.expanduser("~/.claude/projects")
    repo = None if getattr(args, "all", False) else _repo_here()
    everything = P.scan(
        projects,
        min_children=int(cfg.get("minChildren", 2)),
        max_shape_repos=int(cfg.get("maxShapeRepos", 2)),
        ledger_rows=P.ledger_rows(vault),
    )
    return everything, repo


def _match(candidates: list, key: str, repo: str | None):
    """One candidate by key. The repo you stand in breaks a tie, never a miss."""
    exact = [c for c in candidates if c.key == key]
    if len(exact) == 1:
        return exact[0], ""
    if len(exact) > 1:
        here = [c for c in exact if c.repo == repo]
        if len(here) == 1:
            return here[0], ""
        names = ", ".join(sorted(f"{c.repo}:{c.key}" for c in exact))
        return None, f"{key} is a candidate in more than one repo: {names}"
    return None, f"no candidate named {key} (`mnemo procedures --all` lists them)"


def _print_listing(candidates: list, *, repo: str | None, other: int) -> None:
    scope = f"in {repo}" if repo else "across every repo on disk"
    proposals = [c for c in candidates if not c.stated]
    print(f"{len(proposals)} procedure(s) children keep rediscovering {scope} "
          f"and CLAUDE.md does not state\n")
    width = max((len(c.key) for c in candidates), default=0)
    repo_width = 0 if repo else max((len(c.repo) for c in candidates), default=0)
    for c in candidates:
        mark = "stated" if c.stated else "PROPOSE"
        where = "" if repo else f"{c.repo:<{repo_width}}  "
        paid = f"{len(c.rediscovered)}/{c.shape_children}"
        print(f"  {where}{c.key:<{width}}  {mark:7}  {paid:>7} children  {c.command[:58]}")
    print()
    if proposals:
        print("nothing was written — this is a listing only.")
        print("  `mnemo procedures --show KEY` prints the line it proposes, and who paid for it")
        print("  `mnemo procedures --accept KEY` appends that section to the repo's CLAUDE.md")
        print("  `mnemo procedures --drop KEY` takes it out of the queue")
    if other:
        print(f"  ({other} more in other repos — `mnemo procedures --all`)")


def _print_show(candidate) -> None:
    from mnemo.core import procedures as P

    print(f"{candidate.repo}  {candidate.key}"
          f"{'  (already stated in CLAUDE.md)' if candidate.stated else ''}")
    print(f"  {candidate.shape_children} children of {candidate.repo} ran "
          f"`{candidate.shape}`; {len(candidate.rediscovered)} of them worked out what it "
          f"needs here, between {candidate.first_day or '?'} and {candidate.last_day or '?'}.")
    for c in candidate.carriers:
        spellings = "; ".join(f"{value} ×{count}" for value, count in c.values) or "—"
        print(f"    {c.name:20} rediscovered {len(c.rediscovered):3}   kept {c.kept:3}   "
              f"never {c.never:3}   {'stated' if c.stated else 'not in CLAUDE.md'}")
        print(f"    {'':20} values: {spellings[:90]}")
    print(f"    children: {', '.join(candidate.rediscovered[:8])}"
          f"{' …' if len(candidate.rediscovered) > 8 else ''}")
    print()
    print(P.proposed_section(candidate), end="")


@command("procedures")
def cmd_procedures(args: argparse.Namespace) -> int:
    from mnemo import cli
    from mnemo.core import procedures as P

    vault = cli._resolve_vault()

    chosen = [name for name in ("show", "accept", "drop") if getattr(args, name, None)]
    if len(chosen) > 1:
        print(f"pick one action, got: {', '.join('--' + c for c in chosen)}")
        return 1

    everything, repo = _scan(args, vault)
    key = getattr(args, "accept", None) or getattr(args, "drop", None) or getattr(args, "show", None)
    if key:
        candidate, err = _match(everything, key, repo)
        if candidate is None:
            print(err)
            return 1
        if getattr(args, "show", None):
            _print_show(candidate)
            return 0
        if getattr(args, "accept", None):
            result = P.accept(vault, candidate)
        else:
            result = P.drop(vault, candidate)
        print(result.message)
        return 0 if result.ok else 1

    listed = everything if repo is None else [c for c in everything if c.repo == repo]
    if getattr(args, "json", False):
        import json
        from dataclasses import asdict

        print(json.dumps([dict(asdict(c), key=c.key, stated=c.stated, command=c.command)
                          for c in listed], indent=2, ensure_ascii=False))
        return 0
    if not listed:
        where = f"for {repo}" if repo else "anywhere on disk"
        print(f"no procedure is rediscovered by two children {where} "
              "(`mnemo procedures --all` covers every repo)")
        return 0
    _print_listing(listed, repo=repo, other=len(everything) - len(listed))
    return 0
