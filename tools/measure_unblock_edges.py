"""Measure how often a ``blocked -> active`` edge is missed entirely (#176).

Usage:
    PYTHONPATH=src python3 tools/measure_unblock_edges.py [--vault ~/mnemo] [--json]

Read-only: no LLM calls, no writes, no behaviour change.

#176 was re-scoped twice. Option 1 landed in PR #191 (``session_end`` now
actually calls ``detector.sweep``; before that the sweep had a single caller
and never ran unless someone typed ``mnemo sessions`` by hand). PR #199 then
built the consumer, and its measurement removed most of the case for option 3
(``linkScanPath``/``linkScanOffset`` diffing): the consumer runs ``core.learn``,
which re-reads the transcript from disk, so redeeming a marker minutes later
sees *more* of the session than racing the ten-second edge. Not racing was
option 3's whole advantage.

What is left is one question, and it is a measurement rather than a design:

    **How often does an edge get missed entirely?**

A marker redeemed late is fine. A marker that was never written is not — a
session answered and re-blocked between two sweeps leaves nothing on disk, and
no consumer can recover what was never recorded.

Ground truth for "an edge happened" cannot come from the detector's own state
file (that is the thing under test) nor from ``timeline.jsonl`` (it records
``state`` transitions and never mentions ``tempo``) nor from ``atis-latch``
records in the transcript (``atis`` is an opaque token). It comes from the
transcripts themselves: a background session sits at ``tempo=blocked`` while
it waits on the maintainer, and flips to ``active`` the moment their reply
lands. So every genuine human prompt that is not the session's opening task is
one ``blocked -> active`` edge, and the assistant turn before it dates the
moment the session went ``blocked``.

Against that, the triggers that exist today:

- the ``session_end`` hook, once per session that ends — dated by the mtime of
  the per-session briefing it writes under ``bots/*/briefings/sessions/``
- ``mnemo sessions`` typed by hand, which leaves no trace of its own

An edge is **caught** only if a trigger fires inside its window: after the
session went ``blocked`` and before it flipped back. The window closes when
the assistant resumes work, so the edge is live for roughly the assistant's
first-response latency after the reply — measured at ~10s on a real dispatch.
Because hand-typed ``mnemo sessions`` invocations leave no record, this script
reports the ``session_end``-only rate as the floor and states the rest
explicitly rather than guessing at it.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Which ``type: user`` records are a person talking to the session is the
# detector's own predicate, imported so the measurement and the markers it
# measures cannot drift apart. Needs ``PYTHONPATH=src`` (see Usage).
from mnemo.core.sessions.detector import is_human_turn

#: When PR #191 gave ``session_end`` a sweep. Before this, ``detector.sweep``
#: had exactly one caller (``mnemo sessions``, typed by hand), so edges from
#: the earlier era were never catchable by the hook and a 0% rate over them is
#: trivially true. Reported as a separate era rather than averaged in: a single
#: headline percentage over both regimes would overstate what is known about
#: the design shipped today.
PR_191_MERGED = datetime(2026, 9, 12, 21, 30, 41, tzinfo=timezone.utc)

#: How long the edge stays observable after the maintainer's reply lands.
#: Measured at ~10s on a real dispatch (2026-09-12): a session that is
#: answered and then asks a follow-up is back at ``blocked`` within seconds.
#: A sweep later than this records nothing, which is precisely the miss #176
#: asks about.
EDGE_WINDOW_SECONDS = 10.0

def _parse_ts(value: str) -> datetime:
    """Transcript timestamps are ISO-8601 with a ``Z`` suffix."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read_edges(path: Path) -> list[dict]:
    """Every ``blocked -> active`` edge in one transcript.

    Records are sorted by timestamp rather than read in file order: a forked or
    resumed session appends its parent's history, so file order is not time
    order and the assistant turn "before" a prompt would otherwise be wrong.

    The session's opening prompt is not an edge — the session was not blocked
    before it existed. Every later human prompt is, and ``blocked_at`` is the
    end of the assistant activity that preceded it.
    """
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("timestamp"):
                records.append(record)
    records.sort(key=lambda r: r["timestamp"])

    edges: list[dict] = []
    last_assistant: datetime | None = None
    for record in records:
        kind = record.get("type")
        if kind == "assistant":
            last_assistant = _parse_ts(record["timestamp"])
            continue
        if kind != "user":
            continue
        if not is_human_turn(record):
            continue
        if last_assistant is None:
            continue  # the opening task: nothing was blocked yet
        answered_at = _parse_ts(record["timestamp"])
        edges.append({
            "blocked_at": last_assistant,
            "answered_at": answered_at,
            "blocked_seconds": (answered_at - last_assistant).total_seconds(),
        })
    return edges


def read_background_sessions(projects_root: Path) -> dict[str, Path]:
    """Transcript path per background session id under *projects_root*.

    Only ``sessionKind: bg`` sessions are in scope: the detector reads
    ``~/.claude/jobs``, which only background sessions populate. A foreground
    terminal session has no job dir and can never produce a marker, so
    including it would measure a gap the feature does not claim to cover.
    """
    found: dict[str, Path] = {}
    for name in glob.glob(str(projects_root / "*" / "*.jsonl")):
        path = Path(name)
        kind = None
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if record.get("sessionKind"):
                        kind = record["sessionKind"]
                        break
        except OSError:
            continue
        if kind == "bg":
            found[path.stem] = path
    return found


def read_recorded_unblocks(vault_root: Path) -> list[dict]:
    """Every marker the detector has actually written, ever.

    Read for corroboration, not as ground truth: this file is the thing under
    test. If coverage were adequate it would hold roughly one marker per real
    edge; what it holds instead is the headline result.
    """
    path = vault_root / ".mnemo" / "session-queue.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for short_id, entry in (data.get("seen") or {}).items():
        for unblock in entry.get("unblocks", []) or []:
            out.append({"session": short_id, **unblock})
    return out


def read_session_end_triggers(vault_root: Path) -> list[datetime]:
    """When the ``session_end`` hook fired, dated by the briefings it wrote.

    A briefing is written once per session end, so its mtime is the closest
    recorded proxy for the hook firing. This undercounts: ``session_end`` also
    runs for sessions that produce no briefing (``min_mutations`` not met —
    which is exactly the population the marker exists to rescue), and those
    firings still sweep. So the caught-rate computed from these is a floor.
    """
    stamps = []
    for name in glob.glob(str(vault_root / "bots" / "*" / "briefings" / "sessions" / "*.md")):
        try:
            stamps.append(datetime.fromtimestamp(os.path.getmtime(name), timezone.utc))
        except OSError:
            continue
    return sorted(stamps)


def self_exclusion_check(sessions: dict[str, Path]) -> dict:
    """Why the caught rate is 0 rather than merely low.

    ``session_end`` fires when a session *stops*, which is strictly after every
    edge that session produced. So a session can never catch its own edges —
    catching one requires an *unrelated* session to end inside that particular
    10-second window. This is a structural property of the trigger, not a
    frequency that better luck or a longer window would improve, and it is what
    the hook's own docstring predicted: "an edge that opens and closes inside
    another session's lifetime is still missed."

    Reported as edges-needing-a-foreign-end, so the claim is checkable rather
    than asserted.
    """
    rows = []
    for short, path in sorted(sessions.items()):
        edges = read_edges(path)
        if not edges:
            continue
        span = (edges[-1]["answered_at"] - edges[0]["blocked_at"]).total_seconds()
        rows.append({
            "session": short[:8],
            "edges": len(edges),
            "lifetime_hours": span / 3600.0,
            "needs_foreign_session_ends": len(edges),
        })
    return {
        "sessions": rows,
        "edges_requiring_a_foreign_session_end": sum(r["edges"] for r in rows),
    }


def own_end_check(sessions: dict[str, Path]) -> dict:
    """How many edges their *own* session's end could not possibly have caught.

    The sample-independent half of the argument: for a session to catch its own
    edge it must stop within ``EDGE_WINDOW_SECONDS`` of being answered, but it
    was answered in order to keep working. Every edge that is not the session's
    last one is provably out of reach, by arithmetic on that session's own
    timing — no trigger data involved, so this holds for edges not yet recorded
    and does not depend on how many were collected.
    """
    unreachable = 0
    final = 0
    for path in sessions.values():
        edges = read_edges(path)
        if not edges:
            continue
        last = max(e["answered_at"] for e in edges).timestamp()
        for edge in edges:
            if edge["answered_at"].timestamp() + EDGE_WINDOW_SECONDS < last:
                unreachable += 1
            else:
                final += 1
    return {
        "cannot_be_caught_by_own_session_end": unreachable,
        "is_sessions_final_edge": final,
    }


def split_by_era(edges: list[dict]) -> dict:
    """Edges before vs. after ``session_end`` gained a sweep (PR #191).

    A catch rate over the pre-#191 edges says nothing about the design shipped
    today, because the hook did not sweep then. Keeping the eras apart is what
    stops the headline from overstating the evidence.
    """
    before = [e for e in edges if e["answered_at"] < PR_191_MERGED]
    after = [e for e in edges if e["answered_at"] >= PR_191_MERGED]
    return {"before_pr_191": before, "after_pr_191": after}


def classify(edges: list[dict], triggers: list[datetime]) -> dict:
    """Split *edges* into caught and missed, given when sweeps fired.

    An edge is caught when a trigger lands in ``[answered_at, answered_at +
    EDGE_WINDOW_SECONDS]`` — after the flip to ``active`` and before the flip
    back. A trigger during the blocked stretch records ``last_tempo=blocked``
    and is what *arms* the detector; it does not record the edge itself.
    """
    caught, missed = [], []
    for edge in edges:
        start = edge["answered_at"]
        deadline = start.timestamp() + EDGE_WINDOW_SECONDS
        hit = any(start.timestamp() <= t.timestamp() <= deadline for t in triggers)
        (caught if hit else missed).append(edge)
    return {"caught": caught, "missed": missed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default="~/mnemo", help="vault root (default: ~/mnemo)")
    parser.add_argument("--projects", default="~/.claude/projects",
                        help="Claude Code transcript root")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    vault_root = Path(os.path.expanduser(args.vault))
    projects_root = Path(os.path.expanduser(args.projects))

    sessions = read_background_sessions(projects_root)
    triggers = read_session_end_triggers(vault_root)
    recorded = read_recorded_unblocks(vault_root)

    per_session = []
    all_edges: list[dict] = []
    for short, path in sorted(sessions.items()):
        edges = read_edges(path)
        if not edges:
            continue
        all_edges.extend(edges)
        split = classify(edges, triggers)
        per_session.append({
            "session": short[:8],
            "edges": len(edges),
            "caught": len(split["caught"]),
            "missed": len(split["missed"]),
            "median_blocked_seconds": sorted(e["blocked_seconds"] for e in edges)[len(edges) // 2],
        })

    total = classify(all_edges, triggers)
    structural = self_exclusion_check(sessions)
    own_end = own_end_check(sessions)
    eras = split_by_era(all_edges)
    era_stats = {
        name: {
            "edges": len(rows),
            "caught": len(classify(rows, triggers)["caught"]),
        }
        for name, rows in eras.items()
    }
    sensitivity = {}
    global EDGE_WINDOW_SECONDS
    real_window = EDGE_WINDOW_SECONDS
    for window in (10, 60, 300, 900, 3600, 86400):
        EDGE_WINDOW_SECONDS = float(window)
        sensitivity[window] = len(classify(all_edges, triggers)["caught"])
    EDGE_WINDOW_SECONDS = real_window

    summary = {
        "background_sessions": len(sessions),
        "sessions_with_edges": len(per_session),
        "edges_total": len(all_edges),
        "edges_caught_by_session_end": len(total["caught"]),
        "edges_missed": len(total["missed"]),
        "session_end_triggers": len(triggers),
        "edge_window_seconds": EDGE_WINDOW_SECONDS,
        "caught_by_window_seconds": sensitivity,
        "edges_requiring_a_foreign_session_end":
            structural["edges_requiring_a_foreign_session_end"],
        "markers_actually_on_disk": len(recorded),
        "by_era": era_stats,
        "own_end_check": own_end,
    }

    if args.json:
        print(json.dumps({
            "summary": summary,
            "per_session": per_session,
            "structural": structural,
            "recorded_markers": recorded,
        }, indent=2))
        return 0

    print("Unblock edge coverage (#176)")
    print("=" * 64)
    print(f"background sessions found:      {summary['background_sessions']}")
    print(f"  with at least one edge:       {summary['sessions_with_edges']}")
    print(f"real blocked->active edges:     {summary['edges_total']}")
    print(f"session_end firings on record:  {summary['session_end_triggers']}")
    print()
    print("BY ERA -- only the post-#191 rows say anything about today's design:")
    print(f"  before PR #191 (no sweep existed): "
          f"{era_stats['before_pr_191']['edges']:3d} edges, "
          f"{era_stats['before_pr_191']['caught']} caught  <- proves nothing")
    print(f"  after  PR #191 (sweep is wired):   "
          f"{era_stats['after_pr_191']['edges']:3d} edges, "
          f"{era_stats['after_pr_191']['caught']} caught  <- too thin for a rate")
    print()
    print(f"caught by session_end (all eras): {summary['edges_caught_by_session_end']}"
          f" / {summary['edges_total']}   (do not quote as coverage)")
    print()
    print("sample-independent check (arithmetic on each edge's own timing):")
    print(f"  cannot be caught by their OWN session_end: "
          f"{own_end['cannot_be_caught_by_own_session_end']}")
    print(f"  are the session's final edge:              "
          f"{own_end['is_sessions_final_edge']}")
    print()
    print("window sensitivity (is 0 an artefact of the 10s window?):")
    for window, hits in sensitivity.items():
        share = 100.0 * hits / summary["edges_total"] if all_edges else 0.0
        print(f"  window={window:6d}s  caught={hits:3d}  ({share:.1f}%)")
    print()
    print(f"structurally, {structural['edges_requiring_a_foreign_session_end']} of "
          f"{summary['edges_total']} edges could only be caught by an *unrelated*")
    print("session ending inside their 10s window: session_end fires after a")
    print("session stops, hence after every edge that session produced.")
    print()
    print(f"markers actually on disk (all time): {summary['markers_actually_on_disk']}")
    for marker in recorded:
        print(f"  {marker.get('session')}  at={marker.get('at')}  "
              f"extracted={marker.get('extracted')}")
    print()
    print("per session (only those with edges):")
    for row in sorted(per_session, key=lambda r: -r["edges"]):
        print(f"  {row['session']}  edges={row['edges']:3d}  caught={row['caught']:3d}"
              f"  missed={row['missed']:3d}  median blocked={row['median_blocked_seconds']:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
