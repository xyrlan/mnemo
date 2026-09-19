"""Record the moment a blocked session gets answered.

Answering a blocked session is the highest-signal correction the maintainer
produces: they are only consulted when it matters. The detector writes down
which session was answered, and when. It extracts nothing itself —
:mod:`mnemo.core.sessions.unblocks` redeems the markers.

The original note here said the marker existed "so extraction can treat that
transcript region as high signal". That is not what it turned out to be worth
(#195). A briefing is built from the whole transcript in one prompt, with no
notion of an offset or a weight, and its ``## Corrections`` section is already
quote-verified against real user turns, so emphasis was never the constraint.
What the marker actually buys is *reach*: ``session_end`` briefs at the
default ``min_mutations=1``, so a session whose only product is an answer
mutates nothing and is skipped outright. The edge is the cheap selector for
the sessions the automatic path throws away — measured over 206 real
transcripts, 76 have zero mutations but only 15 carry a correction.

**Where the edge is read from.** Not from ``tempo``. The first version of this
module compared ``state.json``'s ``tempo`` against the last value it saw and
recorded the ``blocked -> active`` flip. That flip lasts about ten seconds —
a session that is answered asks its next question and is back at ``blocked``
before anyone looks — and every trigger the sweep rides fires on an event
uncorrelated with that window: ``mnemo sessions`` when a human types it,
``session_end`` when some *other* session stops (a session cannot end inside
its own edge; it was answered in order to keep going). Measured on the real
transcripts (#176, PR #203): 107 of 126 edges were provably out of reach of
their own session's end, and in a twelve-minute window carrying three real
edges an ad-hoc 3s poll loop caught all three while ``session_end`` fired
zero times. No cadence fixes an uncorrelated sampler, and a 3s poll is a
daemon, which this module rules out by design.

So the sweep reads the record that *outlives* the edge: the transcript.
``state.json`` names it (``linkScanPath``), Claude Code only ever appends to
it, and the maintainer's answer is a ``type: user`` record in it forever.
Each session gets a byte bookmark in ``session-queue.json``; a sweep reads
forward from the bookmark and every human turn it finds is one unblock,
however late the sweep runs. The bookmark is this module's own — Claude
Code's ``linkScanOffset`` advances on its own schedule and would skip turns
nobody here has seen.

Which ``type: user`` records are human turns is one predicate,
:func:`is_human_turn`, shared with ``tools/measure_unblock_edges.py`` so the
measurement and the detector cannot drift apart. Tool results arrive as user
records and are the assistant's own loop; task notifications, slash-command
expansions, skill preambles and interrupt markers are Claude Code talking to
itself; a ``SendMessage`` from another session arrives wrapped in
``<cross-session-message>`` and *is* the maintainer answering (that is how a
blocked ``--bg`` child gets unblocked at all). The session's opening prompt is
not an edge — nothing was blocked before it existed — and it is told apart by
the one thing that distinguishes it: no assistant turn precedes it.

It cannot use ``timeline.jsonl``: that file records ``state`` transitions and
never mentions ``tempo`` (measured on a real dispatch, 2026-09-12).

No daemon. The sweep rides triggers that already exist — every ``mnemo
sessions`` invocation and the ``session_end`` hook — mirroring how autopilot
schedules itself. A late sweep is now merely late: consuming a marker re-reads
the transcript from disk (#199), so an answer found an hour later is learned
from just as well as one found in the ten seconds ``tempo`` showed it.

The ``tempo`` comparison is kept only as the fallback for a session whose
transcript cannot be read at all: it is what was there before, and better
than recording nothing.
"""
from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mnemo.core.activity.tail import WINDOW, read_tail
from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.sessions.jobs import Session

STATE_FILENAME = "session-queue.json"

#: How much of an answer a marker keeps. The marker is a pointer, not the
#: record: the consumer re-reads the transcript. The excerpt exists so a
#: human reading ``session-queue.json`` can tell the markers apart.
EXCERPT_CHARS = 200

#: Prompt prefixes that are not the maintainer answering a blocked session.
#: Task notifications, slash-command expansions, skill preambles and interrupt
#: markers all arrive as ``type: user`` records but none of them is a human
#: unblocking anything, and counting them would inflate the edge population
#: with events the detector was never meant to see.
SYNTHETIC_PREFIXES = (
    "<task-notification",
    "<local-command",
    "<command-name",
    "<command-message",
    "<system-reminder",
    "[Request interrupted",
    "Base directory for this skill",
    "# Claude in Chrome browser automation",
    # mnemo's own notice that a dispatched child finished (#357). It arrives
    # as a peer turn, so without this it would read as a person unblocking the
    # parent — and this very sweep runs in the hook that sends it. Spelled out
    # rather than imported from :mod:`mnemo.core.sessions.inbox`: that module
    # imports nothing from here, and a literal keeps it that way. The two are
    # pinned together by a test.
    "<mnemo-child-finished",
    # mnemo waking a child the account's limit stopped (#393). This one lands
    # as `origin.kind: "human"` — `--bg --resume` is the opening prompt's own
    # channel — so without it every wake would be counted as the maintainer
    # answering a blocked session, and a wake is precisely the case where
    # nobody answered anything. Literal for the same reason as the line above;
    # the two are pinned together by a test.
    "<mnemo-resume",
)

#: The byte signature of an assistant record, as Claude Code writes it
#: (compact JSON, no space after the colon). Used only to answer "has this
#: session replied at all yet" for a transcript too large to parse whole.
_ASSISTANT_SIGNATURE = b'"type":"assistant"'

_CROSS_SESSION = re.compile(
    r"<cross-session-message\b[^>]*>(.*?)</cross-session-message>", re.DOTALL
)

#: Claude Code's framing around a peer turn. Measured over the 64 peer turns
#: on this machine (2026-09-15): every one opens with this header, and every
#: socket delivery closes with this trailer paragraph.
_PEER_HEADER = "Another Claude session sent a message:"
_PEER_TRAILER = "\n\nThis came from another Claude session — "


def turn_text(record: dict[str, Any]) -> str | None:
    """The human-visible text of a ``type: user`` record; ``None`` for a
    tool result, which is a user record in shape but the assistant's own loop.
    """
    message = record.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return content if isinstance(content, str) else ""


def is_human_turn(record: dict[str, Any]) -> bool:
    """True when *record* is a person (or a session acting for one) talking
    to this session. The one definition; the measurement tool imports it."""
    if record.get("type") != "user" or record.get("isSidechain"):
        return False
    text = turn_text(record)
    if text is None:
        return False
    stripped = text.lstrip()
    return bool(stripped.strip()) and not stripped.startswith(SYNTHETIC_PREFIXES)


def _assistant_text(record: dict[str, Any]) -> str:
    content = (record.get("message") or {}).get("content")
    if not isinstance(content, list):
        return content if isinstance(content, str) else ""
    return " ".join(
        b.get("text", "") for b in content
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    )


def _excerpt(text: str) -> str:
    """Whitespace collapsed, cut to :data:`EXCERPT_CHARS`."""
    return " ".join(text.split())[:EXCERPT_CHARS]


def _answer_text(text: str) -> str:
    """The answer itself. A ``SendMessage`` arrives inside a wrapper that
    explains to the child where it came from; the wrapper is not the answer.

    A raw write to the inbox socket has no wrapper, and Claude Code frames the
    body itself: a header line before it and a trailer paragraph after (#304).
    Both are stripped by their text, and only where Claude Code puts them —
    the header must open the turn, and the trailer is its last paragraph, so a
    body with blank lines of its own keeps them."""
    match = _CROSS_SESSION.search(text)
    if match:
        return _excerpt(match.group(1))
    body = text.lstrip()
    if body.startswith(_PEER_HEADER):
        body = body[len(_PEER_HEADER):]
        cut = body.rfind(_PEER_TRAILER)
        if cut != -1:
            body = body[:cut]
    return _excerpt(body)


def _has_assistant_turn(path: str) -> bool:
    """Has this session replied at all? A byte scan, no JSON: only asked
    when a transcript is too large for the cold-start window, which is once
    per session and rare."""
    try:
        with open(path, "rb") as fh:
            carry = b""
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    return False
                if _ASSISTANT_SIGNATURE in carry + chunk:
                    return True
                carry = chunk[-len(_ASSISTANT_SIGNATURE):]
    except OSError:
        return False


def _state_path(vault_root: Path) -> Path:
    return vault_root / ".mnemo" / STATE_FILENAME


def _load(vault_root: Path) -> dict[str, Any]:
    """Stored state, or a fresh one. A corrupt file is replaced, not fatal.

    This file is disposable: losing it costs at most some un-extracted
    markers, never a rule or a proposal.
    """
    try:
        data = json.loads(_state_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"seen": {}}
    if not isinstance(data, dict) or not isinstance(data.get("seen"), dict):
        return {"seen": {}}
    return data


def _save(vault_root: Path, data: dict[str, Any]) -> None:
    """Write through :func:`atomic_write_bytes`: it stages on a unique mkstemp
    name (a fixed sibling ``.tmp`` is shared by concurrent sweeps) and retries
    the replace, which Windows can lose several times before winning. It also
    creates the parent directory itself, so this does not.
    """
    body = json.dumps(data, indent=2, ensure_ascii=False)
    atomic_write_bytes(_state_path(vault_root), body.encode("utf-8"))


def _new_turns(entry: dict[str, Any], session: Session) -> list[dict[str, Any]] | None:
    """Every human turn in *session*'s transcript since the bookmark, moving
    the bookmark. ``None`` when there is no transcript to read, which is the
    caller's cue to fall back to the ``tempo`` comparison.

    A first sighting of a transcript — a session never seen before, or one
    whose ``linkScanPath`` moved (a resume writes a new file carrying a copy
    of the old history) — baselines at the end of the file and records
    nothing: what happened before mnemo was looking is history, and turning
    it into markers would hand the consumer one learn call per old turn. The
    same read that baselines settles whether an assistant turn already exists,
    which is what tells the opening prompt apart from an answer later.

    A file that shrank below the bookmark was rotated or truncated: it is
    baselined again, the same way, rather than replaying its tail as new turns.
    """
    path = session.link_scan_path
    if not path:
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None

    offset = entry.get("offset")
    fresh = entry.get("transcript") != path or not isinstance(offset, int)
    if fresh or size < offset:
        events, boundary = read_tail(path, 0)
        primed = any(e.get("type") == "assistant" for e in events)
        if not primed and size > WINDOW:
            primed = _has_assistant_turn(path)
        asked = None
        for event in events:
            if event.get("type") == "assistant":
                text = _assistant_text(event)
                if text.strip():
                    asked = _excerpt(text)
        entry["transcript"] = path
        # The bookmark is the last line boundary the read saw, not the stat'd
        # size: a file caught mid-write would otherwise leave the bookmark
        # inside a record, and the next sweep would drop that record as
        # corrupt — which is a lost turn if it was the answer.
        entry["offset"] = boundary
        entry["primed"] = primed
        entry["asked"] = asked
        return []

    # `window=size` disables the cold-start windowing: a bookmark of 0 here
    # means "seen when the file was empty", and the region since then must be
    # read whole or the opening prompt could be mistaken for an answer.
    events, new_offset = read_tail(path, offset, window=max(size, 1))

    turns: list[dict[str, Any]] = []
    primed = bool(entry.get("primed"))
    asked = entry.get("asked")
    for event in events:
        kind = event.get("type")
        if kind == "assistant":
            primed = True
            text = _assistant_text(event)
            if text.strip():
                asked = _excerpt(text)
        elif primed and is_human_turn(event):
            turns.append({
                "answered_at": event.get("timestamp"),
                "answer": _answer_text(turn_text(event) or ""),
                "needs": asked,
            })
    entry["primed"] = primed
    entry["asked"] = asked
    entry["offset"] = new_offset
    return turns


def sweep(sessions: list[Session], *, vault_root: Path) -> int:
    """Record every human turn that reached a session since the last sweep.
    Returns how many were recorded.

    A sweep that changed nothing does not write. ``mnemo sessions --watch``
    sweeps every two seconds, and most of those ticks find every session
    exactly where the last one left it; writing anyway meant a mkstemp,
    write and replace per tick, and put the atomic writer's
    concurrent-writer retry path on a hot loop for no gain. A transcript that
    grew does move its bookmark, and that is a write — the alternative is
    re-parsing an ever-growing region on every tick.

    The return value is unrelated: it counts unblocks, and a sweep can change
    state (a new session, a tempo move, a bookmark) while recording none.
    """
    data = _load(vault_root)
    before = copy.deepcopy(data)
    seen = data["seen"]
    recorded = 0

    for s in sessions:
        entry = seen.setdefault(s.short_id, {"last_tempo": None, "unblocks": []})
        previous = entry.get("last_tempo")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        turns = _new_turns(entry, s)
        if turns is None:
            # No transcript to read: the tempo comparison is all that is left.
            if previous == "blocked" and s.tempo == "active":
                entry["unblocks"].append({
                    "at": now,
                    "needs": entry.get("last_needs"),
                    "session_id": s.session_id,
                    "link_scan_path": s.link_scan_path,
                    "cwd": s.cwd,
                    "extracted": False,
                })
                recorded += 1
        else:
            for turn in turns:
                entry["unblocks"].append({
                    "at": now,
                    "answered_at": turn["answered_at"],
                    "answer": turn["answer"],
                    "needs": turn["needs"] or entry.get("last_needs"),
                    "session_id": s.session_id,
                    "link_scan_path": s.link_scan_path,
                    "cwd": s.cwd,
                    "extracted": False,
                })
                recorded += 1

        entry["last_tempo"] = s.tempo
        # last_needs outlives the unblock on purpose: a session still blocked
        # needs it on the sweep that finally sees it answered.
        if s.is_blocked and s.needs:
            entry["last_needs"] = s.needs

    # Compared whole rather than tracked with a dirty flag on purpose: a flag
    # has to be set at every mutation site and goes quietly stale the day a
    # field is added to an entry, which would skip a write that mattered. This
    # covers whatever the loop above touches, including fields not yet written.
    if data != before:
        _save(vault_root, data)
    return recorded


def pending_unblocks(*, vault_root: Path) -> list[dict[str, Any]]:
    """Every recorded unblock that extraction has not consumed yet."""
    out: list[dict[str, Any]] = []
    for entry in _load(vault_root)["seen"].values():
        out.extend(u for u in entry.get("unblocks", []) if not u.get("extracted"))
    return out
