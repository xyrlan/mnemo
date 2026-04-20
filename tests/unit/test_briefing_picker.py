"""Briefing picker — selects the most recent briefing for an agent."""
from __future__ import annotations

import os
from pathlib import Path

from mnemo.core import briefing


def _write_briefing(dir_path: Path, session_id: str, *, date: str, body: str = "body") -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / f"{session_id}.md"
    p.write_text(
        "---\n"
        "type: briefing\n"
        f"agent: testagent\n"
        f"session_id: {session_id}\n"
        f"date: {date}\n"
        "duration_minutes: 30\n"
        "---\n\n"
        f"# Briefing — testagent — {session_id}\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return p


def test_picker_returns_none_when_no_briefings(tmp_path: Path) -> None:
    vault = tmp_path
    result = briefing.pick_latest_briefing(vault, agent_name="ghost")
    assert result is None


def test_picker_returns_only_briefing(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "bots" / "myagent" / "briefings" / "sessions"
    p = _write_briefing(sessions_dir, "abc123", date="2026-04-19")
    result = briefing.pick_latest_briefing(tmp_path, agent_name="myagent")
    assert result is not None
    assert result.path == p
    assert result.frontmatter["session_id"] == "abc123"
    assert result.body.startswith("# Briefing — testagent — abc123")


def test_picker_selects_most_recent_date(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "bots" / "myagent" / "briefings" / "sessions"
    _write_briefing(sessions_dir, "old", date="2026-04-10")
    _write_briefing(sessions_dir, "newer", date="2026-04-19")
    _write_briefing(sessions_dir, "middle", date="2026-04-15")
    result = briefing.pick_latest_briefing(tmp_path, agent_name="myagent")
    assert result is not None
    assert result.frontmatter["session_id"] == "newer"


def test_picker_breaks_tie_by_session_id(tmp_path: Path) -> None:
    """When two briefings share the same date, prefer the higher (lexicographic) session_id."""
    sessions_dir = tmp_path / "bots" / "myagent" / "briefings" / "sessions"
    _write_briefing(sessions_dir, "aaaa", date="2026-04-19")
    _write_briefing(sessions_dir, "zzzz", date="2026-04-19")
    result = briefing.pick_latest_briefing(tmp_path, agent_name="myagent")
    assert result is not None
    assert result.frontmatter["session_id"] == "zzzz"


def test_picker_falls_back_to_mtime_when_date_missing(tmp_path: Path) -> None:
    """A briefing without a parseable date is ranked by file mtime."""
    sessions_dir = tmp_path / "bots" / "myagent" / "briefings" / "sessions"
    sessions_dir.mkdir(parents=True)
    older = sessions_dir / "older.md"
    older.write_text("# no frontmatter\nbody\n", encoding="utf-8")
    os.utime(older, (1000, 1000))
    newer = sessions_dir / "newer.md"
    newer.write_text("# no frontmatter\nbody\n", encoding="utf-8")
    os.utime(newer, (2000, 2000))
    result = briefing.pick_latest_briefing(tmp_path, agent_name="myagent")
    assert result is not None
    assert result.path == newer


def test_picker_skips_unreadable_briefings(tmp_path: Path, monkeypatch) -> None:
    """A briefing that raises OSError on read does not crash the picker.

    Note: avoid `chmod(0o000)` — fails as root and on some filesystems.
    Instead, monkeypatch Path.read_text to raise OSError for one specific file.
    """
    sessions_dir = tmp_path / "bots" / "myagent" / "briefings" / "sessions"
    _write_briefing(sessions_dir, "good", date="2026-04-15")
    bad = sessions_dir / "bad.md"
    bad.write_text("---\ndate: 2026-04-19\n---\nbody\n", encoding="utf-8")

    real_read_text = Path.read_text

    def patched_read_text(self, *args, **kwargs):
        if self == bad:
            raise OSError("simulated permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", patched_read_text)
    result = briefing.pick_latest_briefing(tmp_path, agent_name="myagent")
    assert result is not None
    assert result.frontmatter["session_id"] == "good"
