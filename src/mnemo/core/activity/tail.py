"""Read new lines from an append-only transcript, without re-reading the file.

Claude Code writes one JSON object per line and only ever appends, so a byte
offset is a complete bookmark. Worktree children measured 1.6-3.6MB on
2026-09-13 (5-10x the 327KB global median), which is why the cold start takes
a tail window instead of the whole file: four children re-read every 2s would
be ~10MB of JSON parsed per tick to render four lines.

Knows nothing about sessions or activity. Bytes in, event dicts out.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

# Wide enough to hold the ~15 most recent actions, and ~7% of the median
# worktree child. Transcript bytes are dominated by tool_result payloads; few
# exceed 10KB. One constant with a default, not a structural choice.
WINDOW = 262144


def read_tail(
    path: str,
    offset: int,
    window: int = WINDOW,
) -> Tuple[List[Dict[str, Any]], int]:
    """Return (new events, new offset) from *path*, starting at *offset*.

    ``offset == 0`` is a cold start: seek to the last *window* bytes and drop
    the first line, which is almost certainly cut in half. ``offset > 0`` reads
    forward from the bookmark.

    A trailing line with no newline is left unconsumed and does not advance the
    offset — the child may be mid-write, and half a line is worth nothing.

    Every failure returns ``([], offset_or_0)``. A queue that raises because a
    transcript moved is worse than one that shows a session without detail.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], 0

    if size == 0:
        return [], 0

    # The file shrank: a rotation or truncation happened and the old bookmark
    # now points into unrelated bytes. Re-read rather than parse garbage.
    if offset > size:
        offset = 0

    if offset == size:
        return [], offset

    start = offset
    drop_first = False
    if offset == 0 and size > window:
        start = size - window
        drop_first = True

    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            blob = fh.read()
    except OSError:
        return [], 0 if offset == 0 else offset

    consumed = blob.rfind(b"\n")
    if consumed == -1:
        # Not one complete line in the window. Hold the bookmark.
        return [], offset

    complete = blob[: consumed + 1]
    new_offset = start + consumed + 1

    lines = complete.split(b"\n")
    if drop_first:
        lines = lines[1:]

    events = []  # type: List[Dict[str, Any]]
    for raw in lines:
        if not raw.strip():
            continue
        try:
            event = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue  # a half-written or corrupt line costs only itself
        if isinstance(event, dict):
            events.append(event)

    return events, new_offset
