"""Parse ``~/.claude/jobs/*/state.json`` into :class:`Session` records.

The only module that knows the on-disk schema, so an upstream change touches
one file.

``state`` and ``tempo`` are different axes and the distinction carries the
feature:

- ``state`` is the process phase: ``working``, ``done``
- ``tempo`` is whether a human is needed: ``blocked``, ``active``, ``idle``

A session waiting on a question sits at ``state=working, tempo=blocked``.
Branching on ``state`` files it under "working" and the queue stops surfacing
the only sessions it exists to surface. Measured on a real dispatch,
2026-09-12; ``timeline.jsonl`` records ``state`` transitions and never
mentions ``tempo``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def jobs_dir() -> Path:
    """Where Claude Code keeps background-session state."""
    return Path(os.path.expanduser("~/.claude/jobs"))


@dataclass(frozen=True)
class Session:
    """One background session, as much of it as the file actually had."""

    short_id: str
    state: str | None = None
    tempo: str | None = None
    needs: str | None = None
    detail: str | None = None
    suggested_reply: str | None = None
    name: str | None = None
    intent: str | None = None
    cwd: str | None = None
    tokens: int | None = None
    session_id: str | None = None
    link_scan_path: str | None = None
    updated_at: str | None = None
    children: tuple[dict[str, Any], ...] = ()

    @property
    def is_blocked(self) -> bool:
        """True when a human is needed. Driven by ``tempo``, never ``state``."""
        return self.tempo == "blocked"

    @property
    def is_done(self) -> bool:
        return self.state == "done"

    @property
    def label(self) -> str:
        """Best available human-readable name."""
        if self.name:
            return self.name
        if self.intent:
            return self.intent[:40].replace("\n", " ")
        return self.short_id


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse(short_id: str, data: dict[str, Any]) -> Session:
    children = data.get("children")
    return Session(
        short_id=short_id,
        state=_str_or_none(data.get("state")),
        tempo=_str_or_none(data.get("tempo")),
        needs=_str_or_none(data.get("needs")),
        detail=_str_or_none(data.get("detail")),
        suggested_reply=_str_or_none(data.get("suggestedReply")),
        name=_str_or_none(data.get("name")),
        intent=_str_or_none(data.get("intent")),
        cwd=_str_or_none(data.get("cwd")),
        tokens=data.get("tokens") if isinstance(data.get("tokens"), int) else None,
        session_id=_str_or_none(data.get("sessionId")),
        link_scan_path=_str_or_none(data.get("linkScanPath")),
        updated_at=_str_or_none(data.get("updatedAt")),
        children=tuple(c for c in children if isinstance(c, dict)) if isinstance(children, list) else (),
    )


def normalize_cwd(path: str | None) -> str | None:
    """Canonical form of *path*, or ``None`` when there is nothing to compare.

    A worktree, or ``/tmp`` on macOS, reaches one directory through two
    different strings. Comparing them raw empties the queue and the user reads
    that as "nothing is running". ``realpath`` resolves symlinks and strips a
    trailing slash, and a real path always resolves to itself, so normalizing
    a value that is already canonical is a no-op.
    """
    if not path:
        return None
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):  # pragma: no cover - realpath is total on posix
        return path


def read_sessions(root: Path | None = None, *, cwd: str | None = None) -> list[Session]:
    """Every readable background session under *root* (default: real jobs dir).

    Unreadable or malformed entries are skipped, never raised: one corrupt
    file must not take out the whole listing. Pass *cwd* to keep only sessions
    started under that directory; both sides are normalized, and a session
    with no recorded ``cwd`` never matches a scoped query.
    """
    base = jobs_dir() if root is None else root
    if not base.is_dir():
        return []

    scope = normalize_cwd(cwd)

    out: list[Session] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue  # pins.json and friends
        try:
            data = json.loads((entry / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        session = _parse(entry.name, data)
        if cwd is not None and normalize_cwd(session.cwd) != scope:
            continue
        out.append(session)
    return out
