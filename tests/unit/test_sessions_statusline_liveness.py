"""The statusline badge must agree with the list (#196, mislead #3).

``_blocked_segment`` counted every ``tempo=blocked`` session on disk, so a
zombie inflated the badge in every repo, indefinitely. It now counts what
``mnemo sessions`` shows under TE ESPERANDO — the same rule, or the number
disagrees with the screen.

The global scope is *not* what changes here: counting across repos is
deliberate and documented (``statusline.py``). Only liveness is added.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from mnemo import statusline


def _job(root: Path, short_id: str, **fields) -> None:
    d = root / short_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(fields), encoding="utf-8")


def _roster(home: Path, workers: dict[str, dict]) -> None:
    d = home / "daemon"
    d.mkdir(parents=True, exist_ok=True)
    (d / "roster.json").write_text(json.dumps({"workers": workers}), encoding="utf-8")


def test_abandoned_sessions_are_not_counted(tmp_path: Path) -> None:
    jobs_root, home = tmp_path / "jobs", tmp_path / "home"
    _job(jobs_root, "live", state="working", tempo="blocked", needs="q?")
    _job(jobs_root, "dead", state="working", tempo="blocked", needs="q?")
    _roster(home, {"live": {"pid": os.getpid()}})

    assert statusline._blocked_segment(jobs_root, claude_home=home) == "1 esperando"


def test_badge_is_empty_when_every_blocked_session_is_abandoned(tmp_path: Path) -> None:
    jobs_root, home = tmp_path / "jobs", tmp_path / "home"
    _job(jobs_root, "dead", state="working", tempo="blocked", needs="q?")
    _roster(home, {})

    assert statusline._blocked_segment(jobs_root, claude_home=home) == ""


def test_unknown_liveness_still_counts(tmp_path: Path) -> None:
    """No roster: count it. Silently dropping a real request is the worse bug."""
    jobs_root = tmp_path / "jobs"
    _job(jobs_root, "a", state="working", tempo="blocked", needs="q?")

    assert statusline._blocked_segment(jobs_root, claude_home=tmp_path / "nope") == "1 esperando"


def test_counting_across_repos_is_preserved(tmp_path: Path) -> None:
    """The global scope is deliberate; liveness must not narrow it to one cwd."""
    jobs_root, home = tmp_path / "jobs", tmp_path / "home"
    _job(jobs_root, "a", state="working", tempo="blocked", needs="q?", cwd="/repo/a")
    _job(jobs_root, "b", state="working", tempo="blocked", needs="q?", cwd="/repo/b")
    _roster(home, {"a": {"pid": os.getpid()}, "b": {"pid": os.getpid()}})

    assert statusline._blocked_segment(jobs_root, claude_home=home) == "2 esperando"


def test_badge_matches_the_rendered_list(tmp_path: Path) -> None:
    """The regression guard: badge and list must never disagree."""
    from mnemo.core.sessions.jobs import read_sessions
    from mnemo.core.sessions.render import render_queue

    jobs_root, home = tmp_path / "jobs", tmp_path / "home"
    _job(jobs_root, "live", state="working", tempo="blocked", needs="q?")
    _job(jobs_root, "dead", state="working", tempo="blocked", needs="q?")
    _roster(home, {"live": {"pid": os.getpid()}})

    out = render_queue(read_sessions(jobs_root, claude_home=home))
    badge = statusline._blocked_segment(jobs_root, claude_home=home)

    assert "TE ESPERANDO (1)" in out
    assert badge == "1 esperando"
