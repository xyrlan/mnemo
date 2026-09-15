"""Locate historical Claude Code transcripts and map them back to repos.

Claude Code writes ``~/.claude/projects/<dash-encoded-cwd>/<session-id>.jsonl``,
encoding the cwd by replacing every character that is not a letter or digit
with a dash. This module recovers each project directory's cwd and resolves it
to the canonical mnemo agent name, so a worktree and its main checkout land in
the same bucket.

**The cwd comes from the transcript, not from the directory name** (#301). The
encoding cannot be inverted: ``mnemo-wt-200`` and ``mnemo/wt/200`` encode to
the same name, and so does ``.claude`` and ``/claude``. Every dispatch child
works in ``<repo>-wt-<n>``, so the decode tore every child's directory apart,
the torn path never existed, and the child's transcripts were filed under
agent ``200`` instead of ``mnemo``. Claude Code records the real ``cwd`` on
the transcript's own events; measured on 2026-09-15, 166 of 166 populated
project directories carry one within the first eight lines of a transcript.
The decode survives only as the fallback for a directory whose transcripts
record none.

Pure filesystem. No LLM dependency.
"""
from __future__ import annotations

import json
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
    indistinguishable from a separator, so ``-Users-me-github-mnemo-wt-200``
    decodes to a ``mnemo/wt/200`` that never existed. Only a fallback for a
    project directory whose transcripts record no ``cwd``
    (:func:`recorded_cwd` is the source of truth); never use it to *find*
    anything.

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


#: How far into one transcript to look for a ``cwd``. Real transcripts carry
#: it by line 8 (queue-operation and attachment lines can come first); the cap
#: only stops a transcript that never records one from being read whole.
_CWD_SCAN_LINES = 64


def recorded_cwd(jsonl_paths: list[Path]) -> str | None:
    """The ``cwd`` Claude Code wrote into these transcripts, or ``None``.

    The transcripts of one project directory share a cwd, so the first one
    recorded answers for all of them. Measured on 2026-09-15, 158 of 166
    directories name exactly the cwd they record; the other 8 are Claude Code
    worktrees (``<repo>/.claude/worktrees/<name>``) holding a session that
    started in ``<repo>`` and moved in, which records ``<repo>`` first — the
    same canonical agent the worktree resolves to.
    """
    for jsonl in jsonl_paths:
        try:
            with jsonl.open(encoding="utf-8", errors="replace") as fh:
                for index, line in enumerate(fh):
                    if index >= _CWD_SCAN_LINES:
                        break
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    cwd = event.get("cwd") if isinstance(event, dict) else None
                    if isinstance(cwd, str) and cwd:
                        return cwd
        except OSError:
            continue
    return None


def _agent_for_cwd(cwd: str) -> str:
    """Canonical mnemo agent name for a cwd. Seam for tests."""
    from mnemo.core import agent as agent_mod

    return agent_mod.resolve_canonical_agent(cwd).name


def agent_for_cwd(cwd: str) -> str:
    """Canonical agent for *cwd*, including a dispatch worktree that is gone.

    The one resolution both sides of a transcript lookup must share: ``learn``
    names the project from the ``cwd`` it was handed, :func:`find_transcripts`
    names each directory from the ``cwd`` its transcripts recorded, and a
    lookup only resolves when the two agree.

    A dispatch child's tree (``<repo>-wt-<n>``, ``<repo>-wt-c-<slug>``) is
    removed as soon as it is delivered, taking the ``.git`` pointer that
    names its repo with it; canonical resolution then falls back to the tree's
    basename, ``mnemo-wt-200`` — a namespace nothing reads (#225). The name
    still encodes the repo, so a gone tree is folded to its sibling repo
    first, exactly as ``mnemo sessions`` folds it for scoping. Only the shapes
    dispatch itself writes are folded, and only when the repo is there to
    resolve, so a hand-made ``clubinho-old`` is never filed under
    ``clubinho``.
    """
    if not Path(cwd).exists():
        from mnemo.core.dispatch import WORKTREE_SUFFIX, issue_for_cwd

        if issue_for_cwd(cwd) is not None:
            repo = str(cwd).rstrip("/\\").rsplit(WORKTREE_SUFFIX, 1)[0]
            if Path(repo).is_dir():
                cwd = repo
    return _agent_for_cwd(cwd)


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

        # Agent resolution can walk up the filesystem looking for a `.git`
        # root, which is comparatively expensive. A dir with no transcripts
        # can never contribute a result regardless of `project`, so check
        # for transcripts first and skip resolution entirely when there are
        # none.
        #
        # The remaining "waste" — resolving every *populated* directory before
        # the `project` filter discards it — was measured rather than assumed:
        # 9 populated dirs, 117 transcripts, 0.7ms for a filtered call. The
        # cost is a handful of stats per directory, since a Claude project dir
        # is almost always a repo root and `_find_git_root` returns on its
        # first iteration. Filtering by cwd *before* resolving would be the
        # only way to avoid it, and it would be wrong: a worktree encodes to a
        # different cwd and canonicalises to the same agent, which is the
        # whole reason resolution happens here. Nobody waits on this anyway —
        # `session_start` spawns the sweep detached.
        #
        # Reading the cwd out of a transcript (#301) is the other cost, and
        # was measured the same way: 166 populated dirs, 418 transcripts,
        # 16ms decoding names vs 45ms reading cwds, warm. The decode was
        # cheaper because it was wrong: it found 49 `mnemo` transcripts where
        # there are 121, filing the rest under agents such as `200`.
        jsonl_paths = list(project_dir.glob("*.jsonl"))
        if not jsonl_paths:
            continue

        recorded = recorded_cwd(sorted(jsonl_paths))
        if recorded is not None:
            # Exact, so it resolves whether or not the directory still exists
            # — the same call `learn` makes on the cwd a marker recorded.
            cwd = recorded
            try:
                agent = agent_for_cwd(cwd)
            except Exception:
                agent = _fallback_agent(project_dir.name)
        else:
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

        for jsonl in jsonl_paths:
            try:
                mtime = jsonl.stat().st_mtime
            except OSError:
                continue
            out.append(Transcript(path=jsonl, agent=agent, cwd=cwd, mtime=mtime))

    out.sort(key=lambda t: t.mtime, reverse=True)
    if limit is not None:
        out = out[: max(0, int(limit))]
    return out
