"""What a background session is doing, read from its own transcript.

``state.json`` has always handed over ``linkScanPath`` — the absolute path to
the child's ``.jsonl`` — and ``jobs.py:133`` has always parsed it into
``Session.link_scan_path``. Nothing ever opened it: ``detector.py:110``
forwards it into an unblock marker and ``unblocks.py:113`` ignores it in favour
of re-resolving through ``learn()``. This module opens it.

The queue answers *is it alive* (``live``) and *does it need me* (``tempo``).
This answers *is it progressing* — the third axis, and the one that tells a
working child from a stuck one from a looping one.

Offsets are the caller's, held in memory for the life of a ``--watch`` loop.
Nothing here writes to disk: an offset only has value inside a live watch, and
between separate invocations what the maintainer wants is current state, not
the delta since yesterday.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from mnemo.core.activity.exploration import Exploration, Meter
from mnemo.core.activity.summarize import Activity, recent_actions, summarize
from mnemo.core.activity.tail import WINDOW, read_tail

__all__ = [
    "Activity",
    "Exploration",
    "WINDOW",
    "activities_for",
    "activity_for",
    "exploration_for",
    "read_tail",
    "recent_actions",
    "summarize",
]


def activity_for(session, offset: int = 0) -> Tuple[Optional[Activity], int]:
    """Read *session*'s transcript from *offset*; return (activity, new offset).

    ``(None, offset)`` when the session names no transcript, the file is gone,
    or nothing new arrived. A session with no ``link_scan_path`` is normal, not
    an error: not every Claude Code version writes the field.
    """
    path = getattr(session, "link_scan_path", None)
    if not path:
        return None, offset

    events, new_offset = read_tail(path, offset)
    if not events:
        return None, new_offset

    return summarize(events), new_offset


def activities_for(
    sessions: List,
    offsets: Dict[str, int],
    previous: Optional[Dict[str, Activity]] = None,
) -> Dict[str, Activity]:
    """Activity per ``short_id``, advancing *offsets* in place.

    *previous* carries the last tick's result forward: a session that wrote
    nothing since is still doing whatever it was doing, and blinking the column
    back to em-dash would read as "stopped". Sessions with no activity at all
    are simply absent from the result.
    """
    out = {}  # type: Dict[str, Activity]
    for session in sessions:
        short_id = getattr(session, "short_id", None)
        if not short_id:
            continue
        act, new_offset = activity_for(session, offsets.get(short_id, 0))
        if new_offset:
            offsets[short_id] = new_offset
        if act is None and previous:
            act = previous.get(short_id)
        if act is not None:
            out[short_id] = act
    return out


def exploration_for(path: Optional[str], cwd: Optional[str] = None) -> Optional[Exploration]:
    """Measure the transcript at *path* up to its first mutation (#269).

    Reads **forward from byte 0** and stops at the first mutation, unlike
    :func:`read_tail`, which starts from the end: the answer lives at the head
    of the file. Not cheap, and measured rather than assumed — on the 50
    dispatch transcripts of 2026-09-14 the first mutation sat a median 59% of
    the way in (702KB median, 1.27MB max), because the opening turns carry the
    system prompt, skill and tool listings. All 31 sessions in the queue that
    day measured in 135ms, and the queue reads a finished one once per process.

    ``None`` when there is no path or the file cannot be read. A transcript
    with no tool use at all measures as zero uses, not ``None``: it was read.
    """
    import json

    if not path:
        return None
    meter = Meter(cwd)
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break  # a line still being written is not evidence yet
                try:
                    event = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if meter.feed(event):
                    break
    except OSError:
        return None
    return meter.result
