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

from mnemo.core.activity.summarize import Activity, recent_actions, summarize
from mnemo.core.activity.tail import WINDOW, read_tail

__all__ = [
    "Activity",
    "WINDOW",
    "activities_for",
    "activity_for",
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
