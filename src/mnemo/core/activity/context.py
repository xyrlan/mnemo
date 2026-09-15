"""How full a session's context is, read from its own transcript (#307).

``state.json`` carries a ``tokens`` field and the queue used to print it. It
is not the context size. Measured over all 18 jobs on disk on 2026-09-15 it
sat 3-65x below it — ``fee17ecd``: ``tokens`` 14,540, context 92,566 — and it
matches no quantity the transcript offers either (#307 tried four). Same class
as ``detail`` (#293): Claude Code's own number, semantics unknown.

The number ``/context`` prints is the last model turn's input:
``input_tokens + cache_creation_input_tokens + cache_read_input_tokens`` off
the final ``assistant`` event's ``message.usage``. Checked by resuming three
finished sessions with ``claude -p /context --fork-session
--no-session-persistence`` on 2.1.272: 92.6k / 160.2k / 98k against
92,566 / 160,181 / 98,024 read here.

Two shapes the obvious "last assistant event" gets wrong, both measured over
364 transcripts on 2026-09-15:

- **A synthetic turn.** An API error or refusal is written as an assistant
  event with ``model: "<synthetic>"`` and every usage count at 0; 7 of 364
  transcripts *end* on one. ``/context`` on such a session (``900f2ea5``)
  prints the last real turn, 32.1k — not 0.
- **The tail window misses it.** A cold :func:`read_tail` sees the last
  256KB; one 847KB transcript had its last real turn before that. When the
  window holds none, the whole file is read once.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, Optional, Tuple

from mnemo.core.activity.exploration import _context
from mnemo.core.activity.tail import WINDOW, read_tail

SYNTHETIC_MODEL = "<synthetic>"


def last_context(events: Iterable[Dict[str, Any]]) -> Optional[int]:
    """The context size of the last real model turn in *events*, or ``None``."""
    found = None  # type: Optional[int]
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("model") == SYNTHETIC_MODEL:
            continue
        value = _context(message.get("usage"))
        if value is not None:
            found = value
    return found


def context_for(
    path: Optional[str],
    cache: Optional[Dict[str, Tuple[int, Optional[int]]]] = None,
    window: int = WINDOW,
) -> Optional[int]:
    """Context size of the transcript at *path*. Never raises.

    *cache* maps ``path -> (offset, value)`` and lives as long as the caller
    wants it to — a ``--watch`` loop passes one dict for every tick, so a
    running child costs only the bytes it appended since, and a finished one
    costs a ``stat``. Without it every call is a cold read.
    """
    if not path:
        return None
    cache = {} if cache is None else cache
    offset, previous = cache.get(path, (0, None))
    try:
        events, new_offset = read_tail(path, offset, window)
        value = last_context(events)
        if new_offset < offset:
            previous = None  # truncated and re-read: the old value is not this file's
        if value is None and previous is None and offset == 0 and os.path.getsize(path) > window:
            events, new_offset = read_tail(path, 0, window=os.path.getsize(path))
            value = last_context(events)
    except Exception:  # noqa: BLE001 — a transcript costs its own cell, not the queue
        return previous
    if value is None:
        value = previous
    cache[path] = (new_offset, value)
    return value
