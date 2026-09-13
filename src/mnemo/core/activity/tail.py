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

# 256KB: ~7% of the median worktree child (1.6-3.6MB). Measured on real
# transcripts: yields 152-253 EVENTS on the largest files — not "actions",
# since a later task distills many events into one displayed action. A byte
# budget, not a verified count of anything downstream. One constant with a
# default, not a structural choice.
WINDOW = 262144


def read_tail(
    path: str,
    offset: int,
    window: int = WINDOW,
) -> Tuple[List[Dict[str, Any]], int]:
    """Return (new events, new offset) from *path*, starting at *offset*.

    ``offset == 0`` is a cold start: seek to the last *window* bytes. If that
    boundary falls mid-line, the partial head line is dropped; if it happens to
    land exactly on a line start, nothing is dropped. ``offset > 0`` reads
    forward from the bookmark.

    A trailing line with no newline is left unconsumed and does not advance the
    offset — the tail may be one line the child has not finished writing yet,
    and the reader recovers on its own as soon as a newline is appended.

    Failure paths, exactly:
    - the path cannot be stat'd (missing, permission, not-a-regular-file-ish
      errors on some platforms): returns ``([], offset)`` — a transient stat
      failure must not reset a live bookmark, or the consumer would see
      already-rendered events replayed as if they were new activity.
    - the file is empty: returns ``([], 0)``.
    - the file cannot be opened/read after a successful stat (e.g. a
      directory, or removed between the two calls): returns ``([], offset)``
      for the same replay-safety reason.
    - the window (from a cold start) or the bookmark (from a warm start)
      contains no complete line yet: returns ``([], offset)``.

    A file that *shrank* below the bookmark is treated as truncated/rotated
    and the offset resets to 0 (re-read as a cold start). An in-place rewrite
    that stays the same size or grows past the old offset is NOT detected as
    a rewrite — it resumes at the stale byte offset and silently skips
    whatever changed before it. Out of contract; transcripts are append-only
    in practice.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset

    if size == 0:
        return [], 0

    # The file shrank below the bookmark: a rotation or truncation happened
    # and the old bookmark now points past EOF or into unrelated bytes.
    # Re-read rather than parse garbage. (Does NOT catch same-size or grown
    # in-place rewrites — see docstring.)
    if offset > size:
        offset = 0

    if offset == size:
        return [], offset

    if window <= 0:
        # A non-positive window is nonsensical input, not "window of nothing":
        # treating it as a literal 0-or-negative byte budget would make the
        # windowing branch below swallow the whole visible slice as "the
        # partial head to drop" and jump the offset straight to EOF, losing
        # every event in the file permanently on the very first call — worse
        # than the stall this guards against. Fall back to no windowing.
        window = size

    start = offset
    windowed = False
    if offset == 0 and size > window:
        start = size - window
        windowed = True

    # Probe one byte before the window start to tell a clean line boundary
    # from a mid-line cut. Reading from start-1 costs one byte and lets a
    # boundary that lands exactly on a line start keep that whole line,
    # instead of assuming every windowed cold start begins mid-line. A warm
    # start (offset already sits right after a newline, by construction of
    # new_offset below) never needs this — only a cold start that windowed.
    drop_first = windowed
    probe_at = max(0, start - 1) if windowed else start

    try:
        with open(path, "rb") as fh:
            fh.seek(probe_at)
            blob = fh.read()
    except OSError:
        return [], offset

    if drop_first and blob[:1] == b"\n":
        # The byte immediately before the window is itself a newline, so the
        # window starts exactly on a line boundary. Nothing to drop.
        drop_first = False

    if probe_at < start:
        # We read one extra byte to probe; strip it before parsing.
        blob = blob[start - probe_at :]

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
