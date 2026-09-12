"""Record the moment a blocked session gets answered.

Answering a blocked session is the highest-signal correction the maintainer
produces: they are only consulted when it matters. The detector notices the
``tempo: blocked -> active`` edge and writes down where the answer lives, so
extraction can treat that transcript region as high signal. It extracts
nothing itself.

It cannot use ``timeline.jsonl``: that file records ``state`` transitions and
never mentions ``tempo`` (measured on a real dispatch, 2026-09-12). So this
module keeps ``last_tempo`` per session and compares against the current value.

No daemon. The sweep rides triggers that already exist — every ``mnemo
sessions`` invocation and the ``session_end`` hook — mirroring how autopilot
schedules itself. Worst case a sweep is late, never lost: the edge is still
visible on the next one as long as ``tempo`` has not flipped back.
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
