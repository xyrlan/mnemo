"""Turn recorded unblocks into learned rules.

``detector`` records every ``blocked -> active`` edge; until #195 nothing read
those records and nothing ever set ``extracted``, so the list grew forever and
the signal was discarded. This module is the reader.

**What an unblock is actually worth.** The detector's docstring hoped the
marker would let extraction "treat that transcript region as high signal".
There is no plumbing for that and it would not help if there were: a briefing
is built from the whole transcript in one prompt, with no notion of an offset
or a weight, and its ``## Corrections`` section is already quote-verified
against real user turns (``core.corrections.verify``). Emphasis is not the
scarce thing.

The scarce thing is *reaching the session at all*. ``session_end`` briefs with
the default ``min_mutations=1``, and a session whose only product is the
maintainer answering a question mutates no files — so it is skipped outright,
and nothing downstream ever sees it. Measured over 206 real transcripts, 76
(37%) have zero mutations. But only 15 of those carry correction-shaped user
text, so simply lowering the threshold would buy 61 wasted LLM calls to find
15 sessions.

The unblock edge is what separates them. It fires only when a session was
stuck *because it needed the maintainer* and the maintainer answered. So the
consumer is deliberately unremarkable: it runs the ordinary five-minute loop
(``core.learn``, which briefs at ``min_mutations=0`` and extracts scoped to
that briefing) against the sessions the automatic path would otherwise drop.
The marker is a selector, not a weight.

Consuming at the end rather than mid-flip is also why the cadence problem
(#176) does not bite here: ``learn`` re-reads the transcript from disk, so a
marker redeemed minutes later sees *more* of the session than one redeemed
during the ten seconds the edge was open.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mnemo.core.sessions import detector


@dataclass
class ConsumeReport:
    """What one pass over the pending markers did."""

    #: Markers whose session was learned from.
    consumed: int = 0
    #: Markers left pending: the attempt failed for a reason that may not
    #: repeat (a held lock, a transcript not yet flushed).
    failed: int = 0
    #: Markers retired without learning: they can never resolve to a
    #: transcript, so retrying them forever would recreate the append-only
    #: list this module exists to drain.
    skipped: int = 0
    #: Ledger entries the consumed sessions taught, for the caller to print.
    learned: list = field(default_factory=list)
    #: One line per marker that failed, for the caller to print.
    errors: list = field(default_factory=list)


def _pending_with_position(vault_root: Path) -> list[tuple[str, int, dict[str, Any]]]:
    """Every unconsumed marker, tagged with where it lives in the state file.

    Markers are identified by ``(short_id, index)`` rather than by their
    content. Content is not unique: ``at`` has seconds resolution, so a
    session answered twice within one second writes two byte-identical
    entries, and retiring "the matching one" would retire both — discarding a
    correction that was never learned, which is precisely the loss this module
    exists to stop.

    This mirrors :func:`detector.pending_unblocks` rather than calling it
    because that function deliberately returns flat, position-free copies.
    """
    out: list[tuple[str, int, dict[str, Any]]] = []
    for short_id, entry in detector._load(vault_root).get("seen", {}).items():
        for index, unblock in enumerate(entry.get("unblocks", []) or []):
            if not unblock.get("extracted"):
                out.append((short_id, index, unblock))
    return out


def consume(cfg: dict, *, vault_root: Path) -> ConsumeReport:
    """Learn from every session whose unblock has not been consumed yet.

    Marks each one ``extracted`` only on success, so a transient failure is
    retried on the next pass instead of silently dropping the highest-signal
    correction mnemo can observe. Never raises: this rides hooks and CLI
    commands whose own work must not fail because a transcript went missing.
    """
    from mnemo.core import learn as learn_mod

    report = ConsumeReport()
    # The cheap check first, and the one #195 asks to keep wired: it reads the
    # same file and answers "is there anything to do at all".
    if not detector.pending_unblocks(vault_root=vault_root):
        return report

    done: set[tuple[str, int]] = set()

    for short_id, index, entry in _pending_with_position(vault_root):
        session_id = entry.get("session_id")
        cwd = entry.get("cwd")
        if not session_id or not cwd:
            # Unresolvable by construction: `learn` finds a transcript by
            # stem within a cwd's project. Retire it rather than retry it.
            report.skipped += 1
            done.add((short_id, index))
            continue

        try:
            result = learn_mod.learn(cfg, cwd=cwd, session_id=session_id)
        except Exception as exc:  # noqa: BLE001 — a marker must not break the pass
            report.failed += 1
            report.errors.append(f"{session_id}: {exc}")
            continue

        if getattr(result, "error", ""):
            report.failed += 1
            report.errors.append(f"{session_id}: {result.error}")
            continue

        report.consumed += 1
        report.learned.extend(getattr(result, "learned", None) or [])
        done.add((short_id, index))

    if done:
        _mark_extracted(vault_root, done)
    return report


def _mark_extracted(vault_root: Path, done: set[tuple[str, int]]) -> None:
    """Set ``extracted`` on the markers in ``done``.

    Re-reads the state file rather than mutating the copies read earlier: the
    learn calls are LLM-bound and slow, and a sweep may have appended to the
    file meanwhile. Positions are stable under that — the detector only ever
    appends to a session's ``unblocks`` list — so an entry recorded during the
    pass keeps its own index and is simply left pending for the next one.
    """
    data = detector._load(vault_root)
    changed = False
    for short_id, entry in data.get("seen", {}).items():
        unblocks_list = entry.get("unblocks", []) or []
        for index, unblock in enumerate(unblocks_list):
            if (short_id, index) in done and not unblock.get("extracted"):
                unblock["extracted"] = True
                changed = True
    if changed:
        detector._save(vault_root, data)
