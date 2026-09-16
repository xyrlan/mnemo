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

**Retired versus deferred.** A failed marker stays pending so a transient
failure is retried, but "failed" must not mean "forever" or the list grows
without bound. A marker is retired only when its transcript is *gone*: no
``<session_id>.jsonl`` under any directory of Claude Code's projects root.
That is a superset of the only place ``learn`` ever looks (it resolves a
project from ``cwd``, then matches the stem inside it), so a retired marker is
one ``learn`` could never have redeemed — and a live session, whose transcript
Claude Code is still appending to, can never qualify. What does *not* retire
a marker: a deleted ``cwd``, a lookup miss, a held lock. Measured on the real
vault (2026-09-15), all 27 stuck markers had a deleted or dash-named worktree
``cwd`` and all 27 transcripts were still on disk — retiring on the error
would have discarded 27 answers that are learnable once the lookup is fixed.
The bound on the list is Claude Code's own transcript retention.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mnemo.core import errors as errors_mod
from mnemo.core import locks
from mnemo.core.sessions import detector

#: Where a failed redemption is logged in ``.errors.log``.
ERROR_WHERE = "unblocks.consume"

#: Markers one pass redeems, oldest first. The rest wait for the next
#: SessionEnd, which is minutes away, not days.
#:
#: Each marker is a ``learn``: one briefing call plus a scoped extraction, so
#: the cost of a pass is linear in this number and every one of those calls is
#: an LLM round-trip. The 40 markers pending on 2026-09-15 were a backlog
#: accumulated while the cwd lookup was broken (#324) — draining them in one
#: process meant a sweep still running after 180 s, holding a slot in the
#: storm #329 describes. Five is the size at which a pass ends inside the
#: interval between two session ends, so the queue drains over a working
#: afternoon without any single pass being something a user notices.
MAX_PER_PASS = 5

#: Lock directory under ``<vault>/.mnemo``; one sweep at a time, machine-wide.
LOCK_NAME = "unblocks-consume.lock"

#: When a held lock is assumed to belong to a killed process and reclaimed.
#: Sized against :data:`MAX_PER_PASS` learns rather than against impatience —
#: a pass that is merely slow must not have its lock stolen, because the
#: second pass would then re-run the same LLM calls. A pass that ends, or
#: raises, releases the lock on the way out; only a hard kill waits this out.
LOCK_STALE_SECONDS = 30 * 60


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
    #: Markers retired without learning because their transcript no longer
    #: exists anywhere Claude Code keeps transcripts: nothing can ever redeem
    #: them. Distinct from ``failed``, which is retried.
    retired: int = 0
    #: Ledger entries the consumed sessions taught, for the caller to print.
    learned: list = field(default_factory=list)
    #: One line per marker that failed, for the caller to print.
    errors: list = field(default_factory=list)
    #: Markers this pass did not attempt because it hit its bound. They are
    #: still pending; the next SessionEnd picks them up.
    remaining: int = 0
    #: True when another sweep held the lock, so this pass did nothing at all.
    #: Distinct from an empty report, which means there was nothing to do.
    locked: bool = False


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


def lock_path(vault_root: Path) -> Path:
    """The sweep lock for *vault_root*. One directory, created by the winner."""
    return Path(vault_root) / ".mnemo" / LOCK_NAME


def sweep_in_flight(vault_root: Path) -> bool:
    """True when a sweep is believed to be running right now.

    A cheap ``stat`` that lets a caller skip *spawning* a pass that would only
    take the lock, find it held and exit — the SessionEnd hook uses it the way
    it already uses the extraction lock. Never authoritative: the lock below is
    what actually keeps a second pass out, and this races it harmlessly.
    """
    path = lock_path(vault_root)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age < LOCK_STALE_SECONDS


def _oldest_first(
    pending: list[tuple[str, int, dict[str, Any]]],
) -> list[tuple[str, int, dict[str, Any]]]:
    """Pending markers in the order they were recorded.

    ``at`` is an ISO-8601 UTC string written by the detector, so string order
    is time order. A marker from before the field existed sorts first — it is
    by construction the oldest thing in the file, and it is what a bounded pass
    should reach before anything recorded today.
    """
    return sorted(pending, key=lambda item: (str(item[2].get("at") or ""), item[0], item[1]))


def consume(
    cfg: dict, *, vault_root: Path, limit: int = MAX_PER_PASS
) -> ConsumeReport:
    """Learn from the oldest pending unblocks, at most *limit* of them.

    Marks each one ``extracted`` on success, or when its transcript is gone
    for good (:func:`transcript_gone`); any other failure is retried on the
    next pass instead of silently dropping the highest-signal correction mnemo
    can observe. Never raises: this rides hooks and CLI
    commands whose own work must not fail because a transcript went missing.

    **One pass at a time, and a bounded one.** Every mnemo SessionEnd spawns
    this detached, and on 2026-09-15 that meant 42 concurrent sweeps against a
    40-marker backlog — each one taking every marker through a briefing and an
    extraction, all of them `claude` subprocesses, on a machine at load 118
    (#329). The lock makes the second sweep a no-op instead of a duplicate of
    the first; *limit* keeps even the winner's pass short enough to finish.
    ``limit <= 0`` means unbounded, for a maintainer draining a backlog by hand.
    """
    report = ConsumeReport()
    # The cheap check first, and the one #195 asks to keep wired: it reads the
    # same file and answers "is there anything to do at all". Before the lock,
    # so the overwhelmingly common "nothing pending" pass never touches it.
    if not detector.pending_unblocks(vault_root=vault_root):
        return report

    with locks.try_lock(lock_path(vault_root), stale_after=LOCK_STALE_SECONDS) as held:
        if not held:
            report.locked = True
            return report
        return _consume_locked(cfg, vault_root, limit, report)


def _consume_locked(
    cfg: dict, vault_root: Path, limit: int, report: ConsumeReport
) -> ConsumeReport:
    """The pass itself, with the sweep lock held. See :func:`consume`."""
    from mnemo.core import learn as learn_mod

    done: set[tuple[str, int]] = set()
    failures: dict[tuple[str, int], str] = {}

    pending = _oldest_first(_pending_with_position(vault_root))
    if limit > 0 and len(pending) > limit:
        report.remaining = len(pending) - limit
        pending = pending[:limit]

    for short_id, index, entry in pending:
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
            error = str(getattr(result, "error", "") or "")
        except Exception as exc:  # noqa: BLE001 — a marker must not break the pass
            result, error = None, str(exc) or type(exc).__name__

        if not error:
            report.consumed += 1
            report.learned.extend(getattr(result, "learned", None) or [])
            done.add((short_id, index))
            continue

        # Asked only after a failure: a redeemed marker never pays for the scan.
        if transcript_gone(entry):
            report.retired += 1
            done.add((short_id, index))
            continue

        report.failed += 1
        report.errors.append(f"{session_id}: {error}")
        failures[(short_id, index)] = error

    if done or failures:
        _record(vault_root, done, failures)
    return report


def transcript_gone(marker: dict[str, Any]) -> bool:
    """True only when *marker*'s transcript can never be found again — the
    permanent condition a marker retires on.

    Two conditions, both required:

    1. The marker's recorded ``link_scan_path`` sits under the projects root
       this machine scans. It is the path Claude Code itself reported, so this
       proves the root is the right place to look. Without it a relocated
       config dir (``CLAUDE_CONFIG_DIR``) or a marker from another machine
       would make *every* transcript look gone and retire the whole list.
    2. No ``<session_id>.jsonl`` exists in *any* project directory under that
       root. Deliberately wider than ``learn``'s lookup, which only searches
       the project ``cwd`` resolves to: a transcript ``learn`` misses because
       its worktree was deleted, or because a dash in its name does not
       survive the projects-dir encoding, is not gone.

    Answers ``False`` whenever it cannot tell, because retiring is the
    irreversible half of the decision.
    """
    from mnemo.core.backfill import discover

    session_id = marker.get("session_id")
    recorded = marker.get("link_scan_path")
    if not session_id or not recorded:
        return False
    root = discover.projects_root()
    try:
        # `relative_to` raises ValueError outside the root (3.8 has no
        # `is_relative_to`), which lands in the "cannot tell" branch below.
        Path(recorded).resolve().relative_to(root.resolve())
        if not root.is_dir():
            return False
        name = f"{session_id}.jsonl"
        return not any((d / name).is_file() for d in root.iterdir() if d.is_dir())
    except (OSError, ValueError):
        return False


def _record(
    vault_root: Path,
    done: set[tuple[str, int]],
    failures: dict[tuple[str, int], str],
) -> None:
    """Set ``extracted`` on the markers in ``done``; note each failure on its
    marker.

    Re-reads the state file rather than mutating the copies read earlier: the
    learn calls are LLM-bound and slow, and a sweep may have appended to the
    file meanwhile. Positions are stable under that — the detector only ever
    appends to a session's ``unblocks`` list — so an entry recorded during the
    pass keeps its own index and is simply left pending for the next one.

    A failure lands in two places a human looks: ``attempts`` and
    ``last_error`` on the marker in ``session-queue.json``, and ``.errors.log``.
    The log hears about a marker only when its error *changes*: this pass runs
    on every session end, and a marker waiting on a lookup fix would otherwise
    write the same line dozens of times a day and bury everything else in the
    log. And it hears once per pass, not once per marker: a pass is one
    action, and 27 rows from one pass tripped the circuit breaker that pauses
    every hook (#314).
    """
    data = detector._load(vault_root)
    changed = False
    fresh: list[tuple[str, str]] = []
    for short_id, entry in data.get("seen", {}).items():
        unblocks_list = entry.get("unblocks", []) or []
        for index, unblock in enumerate(unblocks_list):
            key = (short_id, index)
            if unblock.get("extracted"):
                continue
            if key in done:
                unblock["extracted"] = True
                changed = True
            elif key in failures:
                error = failures[key]
                if unblock.get("last_error") != error:
                    fresh.append((str(unblock.get("session_id")), error))
                unblock["last_error"] = error
                unblock["attempts"] = int(unblock.get("attempts") or 0) + 1
                changed = True
    if changed:
        detector._save(vault_root, data)
    if fresh:
        errors_mod.log_error(vault_root, ERROR_WHERE, RuntimeError(_failure_summary(fresh)))


def _failure_summary(failures: list[tuple[str, str]]) -> str:
    """One ``.errors.log`` message for every marker a pass newly failed.

    A lone failure keeps the ``<session_id>: <error>`` shape. Several are
    grouped by error, with each session id lifted out of its own message so
    that 27 "no transcript with session id …" failures read as one class, a
    count and the ids to grep for. The count is markers; each id is listed
    once, since one session answered six times holds six markers.
    """
    if len(failures) == 1:
        session_id, error = failures[0]
        return f"{session_id}: {error}"
    groups: dict[str, list[str]] = {}
    for session_id, error in failures:
        shape = error.replace(session_id, "<id>") if session_id else error
        groups.setdefault(shape, []).append(session_id)
    parts = [
        f"{len(ids)} × {shape}: {', '.join(dict.fromkeys(ids))}" for shape, ids in groups.items()
    ]
    return f"{len(failures)} unblock markers deferred — " + " | ".join(parts)
