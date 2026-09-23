"""``mnemo twins`` — read an issue's two runs blind and say which you would merge.

The reading half of #449; ``mnemo dispatch <n> --twins`` is the running half,
and :mod:`mnemo.core.twins` says why each piece is shaped the way it is.

- ``mnemo twins`` lists every pair and how far along it is.
- ``mnemo twins show <pair>`` prints both diffs as ``A`` and ``B``, each with
  its closing report, in an order drawn once per pair, with each run's own
  names scrubbed out.
- ``mnemo twins prefer <pair> A|B|tie`` records the answer — once — and only
  then says which run was which, what reached each one besides its prompt
  (#453), and how to deliver it.

Pipe-safe like ``deliver``: the answer is in the argv, not a keystroke.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


def _status(pair) -> str:
    from mnemo.core import twins

    if len(pair.started) != len(twins.LABELS):
        return f"{len(pair.started)} of {len(twins.LABELS)} started — not a pair"
    if pair.delivered:
        twin = pair.twin(pair.delivered)
        return f"answered {pair.choice or '—'}, delivered {twin.short_id or twin.tag}"
    if pair.choice:
        return f"answered {pair.choice}"
    if twins.still_working(pair):
        return "running"
    return "ready to judge" if not pair.order else "shown, not answered"


def _list(vault) -> int:
    from mnemo.core import twins

    pairs = twins.read_pairs(vault)
    if not pairs:
        print("no pairs — `mnemo dispatch <issue> --twins` runs one")
        return 0
    for pair in pairs.values():
        print(f"{pair.pair}  #{pair.issue}  {pair.created_at[:10]}  {_status(pair)}")
    return 0


def _reveal(pair) -> None:
    """Which run each label was, now that the answer is on record."""
    from mnemo.core import twins

    for label, tag in zip(twins.LABELS, pair.order):
        twin = pair.twin(tag)
        print(f"  {label} was {twin.short_id or '(id unknown)'}  {twin.tree}")
        # After the answer, like the names: what reached a twin mid-run is
        # for the measurement, not for the judgment (#453).
        found = twins.describe(pair.conditions.get(tag))
        if found:
            print(f"    {found}")
    if pair.preferred:
        chosen = pair.twin(pair.preferred)
        name = chosen.short_id or f"#{pair.issue}-{chosen.tag}"
        print(f"  deliver it: mnemo deliver {name}")
    else:
        names = " or ".join(
            pair.twin(tag).short_id or f"#{pair.issue}-{tag}" for tag in pair.order
        )
        print(f"  a tie: deliver either ({names}), or neither")


@command("twins")
def cmd_twins(args: argparse.Namespace) -> int:
    """List pairs, show one blind, or record the answer for one."""
    from mnemo.core import twins

    vault = twins.default_vault()
    action = getattr(args, "action", None) or "list"
    name = getattr(args, "pair", None)
    if action == "list":
        return _list(vault)
    if not name:
        print(f"mnemo twins {action} needs a PAIR — `mnemo twins` lists them")
        return 1
    try:
        if action == "show":
            print(twins.show(vault, name), end="")
            return 0
        answer = getattr(args, "answer", None)
        if not answer:
            print("say which you would merge: A, B or tie")
            return 1
        pair = twins.prefer(vault, name, answer)
    except twins.TwinsError as exc:
        print(str(exc))
        return 1
    print(f"pair {pair.pair}: recorded {pair.choice}")
    _reveal(pair)
    return 0
