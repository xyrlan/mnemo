"""Locate historical Claude Code transcripts and map them back to repos.

Claude Code writes ``~/.claude/projects/<dash-encoded-cwd>/<session-id>.jsonl``,
encoding the cwd by replacing every path separator with a dash. This module
runs that decode in reverse and resolves each cwd to the canonical mnemo agent
name, so a worktree and its main checkout land in the same bucket.

Pure filesystem. No LLM dependency.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:$")


@dataclass(frozen=True)
class Transcript:
    path: Path
    agent: str
    cwd: str
    mtime: float


def projects_root() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "projects"


def decode_project_dir(*, encoding: str) -> str:
    """Turn ``-Users-me-github-repo`` back into ``/Users/me/github/repo``.

    Lossy by nature — a directory whose real name contains a dash is
    indistinguishable from a separator. That is acceptable here: the decoded
    cwd is only used to derive an agent name, and a cwd that no longer exists
    falls back to a name derived from the encoded string itself.

    Windows note: the encoder in ``session_end.py`` replaces both ``os.sep``
    and ``os.altsep`` with ``-``, so a Windows cwd like ``C:\\Users\\me\\repo``
    encodes to ``C:-Users-me-repo`` — the drive letter's colon is not a
    separator and survives untouched. A plain ``"/" + "-".join(...)`` would
    wrongly prepend a leading slash ahead of the drive letter and produce an
    invalid path (``/C:/Users/me/repo``). Detect that case and join without
    the leading slash instead.
    """
    parts = [part for part in encoding.split("-") if part]
    if not parts:
        return "/"
    if _DRIVE_LETTER.match(parts[0]):
        return "/".join(parts)
    return "/" + "/".join(parts)


def _agent_for_cwd(cwd: str) -> str:
    """Canonical mnemo agent name for a cwd. Seam for tests."""
    from mnemo.core import agent as agent_mod

    return agent_mod.resolve_canonical_agent(cwd).name


def _fallback_agent(encoding: str) -> str:
    parts = [p for p in encoding.split("-") if p]
    return parts[-1] if parts else "unknown"


def find_transcripts(
    *,
    project: str | None = None,
    limit: int | None = None,
) -> list[Transcript]:
    """Return transcripts newest-first, optionally filtered and capped.

    ``project`` matches the resolved agent name exactly. ``limit`` is applied
    *after* sorting and filtering, so it always yields the most recent N of
    whatever the filter selected.
    """
    root = projects_root()
    if not root.is_dir():
        return []

    out: list[Transcript] = []
    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir():
            continue
        cwd = decode_project_dir(encoding=project_dir.name)
        if Path(cwd).is_dir():
            try:
                agent = _agent_for_cwd(cwd)
            except Exception:
                agent = _fallback_agent(project_dir.name)
        else:
            agent = _fallback_agent(project_dir.name)

        if project is not None and agent != project:
            continue

        for jsonl in project_dir.glob("*.jsonl"):
            try:
                mtime = jsonl.stat().st_mtime
            except OSError:
                continue
            out.append(Transcript(path=jsonl, agent=agent, cwd=cwd, mtime=mtime))

    out.sort(key=lambda t: t.mtime, reverse=True)
    if limit is not None:
        out = out[: max(0, int(limit))]
    return out
