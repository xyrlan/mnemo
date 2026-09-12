"""Record the moment a blocked session gets answered.

Answering a blocked session is the highest-signal correction the maintainer
produces: they are only consulted when it matters. The detector notices the
``tempo: blocked -> active`` edge and writes down which session was answered.
It extracts nothing itself — :mod:`mnemo.core.sessions.unblocks` redeems the
markers.

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

It cannot use ``timeline.jsonl``: that file records ``state`` transitions and
never mentions ``tempo`` (measured on a real dispatch, 2026-09-12). So this
module keeps ``last_tempo`` per session and compares against the current value.

No daemon. The sweep rides triggers that already exist — every ``mnemo
sessions`` invocation and the ``session_end`` hook — mirroring how autopilot
schedules itself.

**A late sweep loses the edge.** The original note here claimed the opposite,
on the assumption that ``tempo`` stays ``active`` until someone looks. It does
not: a session that is answered and then asks a follow-up is back at
``blocked`` within seconds. Measured on a real dispatch (2026-09-12) the edge
lasted ~10s, and a sweep run after the flip-back recorded nothing — the
qualifier "as long as ``tempo`` has not flipped back" is doing all the work,
and in practice it usually has. So these triggers are a floor, not a
guarantee, and the sighting rate is bounded by how often they happen to fire.

Closing that gap means not depending on the edge at all: ``state.json`` also
carries ``linkScanPath`` and ``linkScanOffset``, which bookmark how far the
transcript has been consumed and let a consumer diff the region instead of
having to observe the process mid-flip. See #176.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.sessions.jobs import Session

STATE_FILENAME = "session-queue.json"


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


def sweep(sessions: list[Session], *, vault_root: Path) -> int:
    """Record any ``blocked -> active`` edge. Returns how many were recorded.

    A sweep that changed nothing does not write. ``mnemo sessions --watch``
    sweeps every two seconds, and most of those ticks find every session
    exactly where the last one left it; writing anyway meant a mkstemp,
    write and replace per tick, and put the atomic writer's
    concurrent-writer retry path on a hot loop for no gain.

    The return value is unrelated: it counts unblocks, and a sweep can change
    state (a new session, a tempo move) while recording none.
    """
    data = _load(vault_root)
    before = copy.deepcopy(data)
    seen = data["seen"]
    recorded = 0

    for s in sessions:
        entry = seen.setdefault(s.short_id, {"last_tempo": None, "unblocks": []})
        previous = entry.get("last_tempo")
        if previous == "blocked" and s.tempo == "active":
            entry["unblocks"].append({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "needs": entry.get("last_needs"),
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
