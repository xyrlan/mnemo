"""How full a session's context is, and what filled it, read from its own transcript.

**The size (#307).** ``state.json`` carries a ``tokens`` field and the queue
used to print it. It is not the context size. Measured over all 18 jobs on
disk on 2026-09-15 it sat 3-65x below it — ``fee17ecd``: ``tokens`` 14,540,
context 92,566 — and it matches no quantity the transcript offers either (#307
tried four). Same class as ``detail`` (#293): Claude Code's own number,
semantics unknown.

The number ``/context`` prints is the last model turn's input:
``input_tokens + cache_creation_input_tokens + cache_read_input_tokens`` off
the final ``assistant`` event's ``message.usage``. Checked by resuming three
finished sessions with ``claude -p /context --fork-session
--no-session-persistence`` on 2.1.272: 92.6k / 160.2k / 98k against
92,566 / 160,181 / 98,024 read here.

An API error or refusal is written as an assistant event with ``model:
"<synthetic>"`` and every usage count at 0; 7 of 364 transcripts *end* on one.
``/context`` on such a session (``900f2ea5``) prints the last real turn, 32.1k
— not 0 — and so does this.

**What filled it (#308).** Per tool, the tokens its results put in the
context. Not the way ``/context`` estimates it, because that estimate is wrong
in both directions, and its own headline example is the proof:

- ``/context`` (2.1.272, read from the binary) sums
  ``round(len(JSON.stringify(block)) / 4)`` over every ``tool_use`` and
  ``tool_result`` block, and divides by the *window*, not by the context. The
  "Read results using 2.1m tokens (207%)" the maintainer saw on 2026-09-15
  matches ``289faa94`` to the percent (2,070,770 of a 1m window), a session
  whose whole context was 817,747. Of those 2.07m, 2,068,982 are the base64 of
  23 screenshots: an image costs its pixels, not its bytes. Measured from the
  context growth of the turn that received it, three PNGs cost 1,891 / 705 /
  565 tokens against 111,288 / 30,977 / 24,287 for their base64 by chars/4 —
  and ``w·h/750`` from the PNG header gives 1,920 / 709 / 554.
- On text, chars/4 is low by ~44%. Over 2,612 turns across 404 transcripts
  whose only new input was tool-result text of ≥3,000 chars, the growth of the
  context (next turn's input − this turn's input − this turn's output) came to
  2.22 chars per token on claude-opus-5 (n=1,305), 2.30 on claude-fable-5-1
  (n=811), 2.29 on claude-fable-5 (n=426), 1.9-2.1 on opus-4-7/4-8 and
  sonnet-5 (n=70). Per result the ratio runs 1.87-2.67 (p10-p90).

So text counts at :data:`CHARS_PER_TOKEN` (2.25), a PNG at its pixels, any
other image at :data:`IMAGE_TOKENS`, and the estimate for one turn's results is
capped at how much that turn actually grew the context — which no estimate can
exceed, and which is what pulls a wrong image guess back to the truth. The
residual error on text is the per-result spread above (±~17% per result, less
over a session), plus two things this does not see:

- **Results since the last turn.** They are not in the context the size
  describes yet, so they are not counted until the next turn reads them.
- **Content Claude Code later clears.** The transcript keeps every result as
  written. 28 of 404 transcripts show the context *shrinking* between turns
  (by 116 to 51,978 tokens; none a compaction — no ``compact_boundary`` exists
  on disk — and what was dropped is not recorded); after one, the sums may
  overstate what is still there. None of the 24
  background jobs on disk on 2026-09-15 had a shrink.

A tool's *call* (``Write``'s content, ``Edit``'s strings) is model output, not
a result, and is not counted.
"""
from __future__ import annotations

import base64
import json
import struct
import sys
from typing import Any, Dict, Iterable, Optional, Tuple

from mnemo.core.activity.exploration import _context
from mnemo.core.activity.tail import read_tail

SYNTHETIC_MODEL = "<synthetic>"

#: Characters of tool-result text per token. Measured, not Claude Code's 4:
#: see the module docstring for the 2,612 turns behind it.
CHARS_PER_TOKEN = 2.25

#: An image whose size cannot be read off its header (a JPEG, a truncated
#: block). Measured screenshots cost 565-1,891; the turn cap corrects the rest.
IMAGE_TOKENS = 1600

#: Anthropic's image cost: one token per 750 pixels.
PIXELS_PER_TOKEN = 750

_PNG = b"\x89PNG\r\n\x1a\n"


def _image_tokens(source: Any) -> float:
    """What one image block costs: ``w·h/750`` for a PNG, a flat guess otherwise."""
    data = source.get("data") if isinstance(source, dict) else None
    if isinstance(data, str):
        try:
            head = base64.b64decode(data[:32])  # 24 bytes: signature + IHDR size
        except (ValueError, TypeError):
            head = b""
        if head[:8] == _PNG and len(head) >= 24:
            width, height = struct.unpack(">II", head[16:24])
            if width and height:
                return width * height / PIXELS_PER_TOKEN
    return IMAGE_TOKENS


def _result_tokens(content: Any) -> float:
    """Estimated tokens of one ``tool_result`` block's ``content``."""
    if isinstance(content, str):
        return len(content) / CHARS_PER_TOKEN
    if not isinstance(content, list):
        return 0.0
    total = 0.0
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "image":
            total += _image_tokens(block.get("source"))
        elif kind == "text":
            text = block.get("text")
            total += len(text) / CHARS_PER_TOKEN if isinstance(text, str) else 0.0
        else:  # tool_reference, document: rare, and small next to the rest
            total += len(json.dumps(block, ensure_ascii=False)) / CHARS_PER_TOKEN
    return total


class ContextMeter:
    """Feed a transcript's events in order; read :attr:`context` and :meth:`breakdown`.

    Carries everything a later read needs — tool names by ``tool_use`` id, the
    last real turn, results not yet read by a turn — so a watch loop feeds it
    only the lines appended since.
    """

    def __init__(self) -> None:
        #: Input tokens of the last real model turn, or ``None`` before one.
        self.context = None  # type: Optional[int]
        self._tools = {}  # type: Dict[str, float]
        self._names = {}  # type: Dict[str, str]
        self._turn = None  # type: Optional[Tuple[Any, int, int]]  # (message id, input, output)
        self._pending = {}  # type: Dict[str, float]

    def feed(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        message = event.get("message")
        if not isinstance(message, dict):
            return
        kind = event.get("type")
        if kind == "assistant":
            self._assistant(message)
        elif kind == "user":
            self._user(message)

    def _assistant(self, message: Dict[str, Any]) -> None:
        content = message.get("content")
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name")
                self._names[block.get("id")] = name if isinstance(name, str) and name else "unknown"
        if message.get("model") == SYNTHETIC_MODEL:
            return  # never reached the model: what it was sent arrives with the next real turn
        usage = message.get("usage")
        value = _context(usage)
        if value is None:
            return
        # One API response is written as one event per content block, each
        # with the same id and usage — and with parallel tool calls, results
        # land between them. Only a new id is a new turn.
        if self._turn is not None and message.get("id") == self._turn[0]:
            return
        growth = value if self._turn is None else value - self._turn[1] - self._turn[2]
        self._settle(growth)
        output = usage.get("output_tokens") if isinstance(usage, dict) else None
        self._turn = (message.get("id"), value, output if isinstance(output, int) else 0)
        self.context = value

    def _user(self, message: Dict[str, Any]) -> None:
        content = message.get("content")
        for block in content if isinstance(content, list) else ():
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            name = self._names.pop(block.get("tool_use_id"), "unknown")
            self._pending[name] = self._pending.get(name, 0.0) + _result_tokens(block.get("content"))

    def _settle(self, growth: int) -> None:
        """Credit the results this turn read, never more than the turn grew by."""
        estimate = sum(self._pending.values())
        # A turn that shrank the context cleared something; what it cleared is
        # not knowable here, so the estimate stands uncapped.
        scale = growth / estimate if 0 < growth < estimate else 1.0
        for name, tokens in self._pending.items():
            self._tools[name] = self._tools.get(name, 0.0) + tokens * scale
        self._pending = {}

    def breakdown(self) -> Dict[str, int]:
        """``{tool name: tokens}``, largest first, tools under one token left out."""
        rounded = ((name, int(round(tokens))) for name, tokens in self._tools.items())
        return dict(sorted(((n, t) for n, t in rounded if t > 0), key=lambda kv: (-kv[1], kv[0])))


def last_context(events: Iterable[Dict[str, Any]]) -> Optional[int]:
    """The context size of the last real model turn in *events*, or ``None``."""
    meter = ContextMeter()
    for event in events:
        meter.feed(event)
    return meter.context


def measure(
    path: Optional[str],
    cache: Optional[Dict[str, Tuple[int, ContextMeter]]] = None,
) -> Tuple[Optional[int], Dict[str, int]]:
    """``(context size, breakdown)`` of the transcript at *path*. Never raises.

    One read gives both. The whole file is read, not a tail: what filled the
    context goes back to its first turn. Measured on 2026-09-15, parsing all 24
    job transcripts on disk (20.5MB) cold took 0.045s.

    *cache* maps ``path -> (offset, meter)`` and lives as long as the caller
    wants it to — a ``--watch`` loop passes one dict for every tick, so a
    running child costs only the bytes it appended since, and a finished one
    costs a ``stat``. Without it every call is a cold read.
    """
    if not path:
        return None, {}
    cache = {} if cache is None else cache
    offset, meter = cache.get(path, (0, ContextMeter()))
    try:
        events, new_offset = read_tail(path, offset, window=sys.maxsize)
        if new_offset < offset:
            meter = ContextMeter()  # truncated and re-read: the old sums are not this file's
        for event in events:
            meter.feed(event)
    except Exception:  # noqa: BLE001 — a transcript costs its own cell, not the queue
        # The meter may hold part of this read; the next call starts cold
        # rather than feed those events a second time.
        cache.pop(path, None)
        return meter.context, meter.breakdown()
    cache[path] = (new_offset, meter)
    return meter.context, meter.breakdown()


def context_for(
    path: Optional[str],
    cache: Optional[Dict[str, Tuple[int, ContextMeter]]] = None,
) -> Optional[int]:
    """Context size of the transcript at *path*; see :func:`measure`. Never raises."""
    return measure(path, cache)[0]
