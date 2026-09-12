"""Record the moment a blocked session gets answered.

Answering a blocked session is the highest-signal correction the maintainer
produces: they are only consulted when it matters. The detector notices the
``tempo: blocked -> active`` edge and writes down where the answer lives, so
extraction can treat that transcript region as high signal. It extracts
nothing itself.

It cannot use ``timeline.jsonl``: that file records ``state`` transitions and
never mentions ``tempo`` (measured on a real dispatch, 2026-09-12). So this
module keeps ``lastTempo`` per session and compares against the current value.

No daemon. The sweep rides triggers that already exist — every ``mnemo
sessions`` invocation and the ``session_end`` hook — mirroring how autopilot
schedules itself. Worst case a sweep is late, never lost: the edge is still
visible on the next one as long as ``tempo`` has not flipped back.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    path = _state_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def sweep(sessions: list[Session], *, vault_root: Path) -> int:
    """Record any ``blocked -> active`` edge. Returns how many were recorded."""
    data = _load(vault_root)
    seen = data["seen"]
    recorded = 0

    for s in sessions:
        entry = seen.setdefault(s.short_id, {"lastTempo": None, "unblocks": []})
        previous = entry.get("lastTempo")
        if previous == "blocked" and s.tempo == "active":
            entry["unblocks"].append({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "needs": entry.get("lastNeeds"),
                "sessionId": s.session_id,
                "linkScanPath": s.link_scan_path,
                "cwd": s.cwd,
                "extracted": False,
            })
            recorded += 1
        entry["lastTempo"] = s.tempo
        if s.is_blocked and s.needs:
            entry["lastNeeds"] = s.needs

    _save(vault_root, data)
    return recorded


def pending_unblocks(*, vault_root: Path) -> list[dict[str, Any]]:
    """Every recorded unblock that extraction has not consumed yet."""
    out: list[dict[str, Any]] = []
    for entry in _load(vault_root)["seen"].values():
        out.extend(u for u in entry.get("unblocks", []) if not u.get("extracted"))
    return out
