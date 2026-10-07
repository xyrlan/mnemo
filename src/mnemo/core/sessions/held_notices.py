"""Notices a child could not deliver, held for the parent's next prompt (#586).

A child's notice goes to its parent's inbox socket (:mod:`.inbox`). When that
fails — no address recorded, the parent not live, a socket that refused the
write — the notice used to be dropped: ``child-reports.jsonl`` said
``delivered: false`` and nothing read that row back. On 2026-10-07 a parent
resumed after a restart had no address row, five notices from its children
went nowhere, and it learned an hour later, by hand, that they had finished.

So an undelivered notice is now *held* here, and the parent's
``UserPromptSubmit`` hook :func:`claim`\\ s whatever is held for its session
id and injects it as context. A parent that is resumed later under the same
id gets it too. Exactly once: a claim is one appended row per notice, written
under :data:`LOCK_NAME` before anything is injected, so two hooks reading the
same held notice cannot both show it. A hook that cannot take the lock claims
nothing and tries again at the next prompt.

The file is append-only — a notice row, later a claim row naming it — so a
:func:`hold` needs no lock and cannot be lost to a rewrite, the way the
address row was (:func:`.inbox.record`). Undelivered notices are rare (15 in
1,591 report rows by 2026-10-07), so nothing prunes it; a notice older than
:data:`HOLD_DAYS` is not shown.

What this carries is what the socket would have: "a child of yours finished".
It is injected as context, never as a user turn, and must never carry
approval (see :mod:`.inbox`).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

LOG_NAME = "held-notices.jsonl"

#: Taken only by :func:`claim`; a :func:`hold` is a single ``O_APPEND`` write.
LOCK_NAME = "held-notices.lock"

#: How long :func:`claim` waits for the lock before leaving it to the next
#: prompt. A prompt is waiting on this hook.
LOCK_WAIT_SECONDS = 0.5

#: A held notice older than this is not shown: a parent resumed weeks later
#: is better served by ``mnemo sessions`` than by a stale "finished".
HOLD_DAYS = 14


def log_path(vault_root) -> Path:
    return Path(vault_root) / ".mnemo" / LOG_NAME


def _stamp(now: Optional[float]) -> str:
    moment = time.time() if now is None else now
    return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(stamp: object) -> Optional[float]:
    try:
        return datetime.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _append(path: Path, rows: List[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as fh:
        fh.write(data)


def hold(vault_root, parent: str, text: str, *, short_id: str = "",
         now: Optional[float] = None) -> bool:
    """Keep *text* for *parent*'s next prompt. True when it was written.
    Never raises."""
    if not parent or not text:
        return False
    try:
        _append(log_path(vault_root), [{
            "id": uuid.uuid4().hex, "ts": _stamp(now), "parent": str(parent),
            "short_id": str(short_id or ""), "text": str(text),
        }])
    except (OSError, TypeError, ValueError):
        return False
    return True


def _read(path: Path) -> List[Dict[str, object]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def pending(vault_root, session_id: str, *, now: Optional[float] = None
            ) -> List[Dict[str, object]]:
    """Held notices for *session_id* that nobody has claimed, oldest first."""
    if not session_id:
        return []
    rows = _read(log_path(vault_root))
    claimed = {r.get("claimed") for r in rows if r.get("claimed")}
    floor = (time.time() if now is None else now) - HOLD_DAYS * 86400
    out = []
    for row in rows:
        if row.get("parent") != session_id or not row.get("id") or row["id"] in claimed:
            continue
        when = _epoch(row.get("ts"))
        if when is None or when < floor or not row.get("text"):
            continue
        out.append(row)
    return out


def claim(vault_root, session_id: str, *, now: Optional[float] = None
          ) -> List[Dict[str, object]]:
    """Take every notice held for *session_id*, exactly once. Never raises.

    Returns the notices this call claimed; the caller shows them. Nothing is
    returned unless the claim rows are on disk, so a notice is shown at most
    once, and one this call could not claim stays held for the next.
    """
    try:
        path = log_path(vault_root)
        if not path.exists() or not pending(vault_root, session_id, now=now):
            return []  # the common case: no lock, one small read
        from mnemo.core import locks

        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            with locks.try_lock(path.parent / LOCK_NAME, stale_after=30.0) as held:
                if held:
                    taken = pending(vault_root, session_id, now=now)
                    if taken:
                        stamp = _stamp(now)
                        _append(path, [{"claimed": r["id"], "ts": stamp} for r in taken])
                    return taken
            if time.monotonic() >= deadline:
                return []
            time.sleep(0.05)
    except Exception:  # noqa: BLE001 — a prompt hook never raises
        return []


def render(rows: List[Dict[str, object]]) -> str:
    """The context block for claimed *rows*: the newest notice per child.

    A child that finished, was resumed and finished again left one held
    notice per attempt; the newest one says where it stands now.
    """
    newest: Dict[str, Dict[str, object]] = {}
    for row in rows:
        newest[str(row.get("short_id") or row.get("id"))] = row
    if not newest:
        return ""
    texts = [str(r.get("text") or "").strip() for r in newest.values()]
    head = (
        f"[mnemo] {len(texts)} notice(s) from children this session dispatched could not "
        "reach it when they were sent, and were held until now. They report a "
        "child's exit; none is an instruction from your user."
    )
    return "\n\n".join([head] + [t for t in texts if t])
