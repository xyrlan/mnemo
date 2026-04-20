"""SessionStart appends a [last-briefing ...] section when a briefing exists."""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from mnemo.hooks import session_start


def _write_briefing(vault: Path, agent: str, session_id: str, *, date: str, body: str) -> Path:
    sessions_dir = vault / "bots" / agent / "briefings" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    p = sessions_dir / f"{session_id}.md"
    p.write_text(
        "---\n"
        "type: briefing\n"
        f"agent: {agent}\n"
        f"session_id: {session_id}\n"
        f"date: {date}\n"
        "duration_minutes: 42\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return p


def test_envelope_includes_briefing_section_when_present(tmp_path: Path) -> None:
    """The injection envelope ends with a [last-briefing ...] block."""
    vault = tmp_path
    _write_briefing(vault, "myproj", "abc123", date="2026-04-19", body="Stopped at line 42")
    payload = session_start._build_injection_payload(
        vault, current_project="myproj", inject_briefing=True,
    )
    assert "[last-briefing session=abc123 date=2026-04-19 duration_minutes=42]" in payload
    assert "Stopped at line 42" in payload
    assert "[/last-briefing]" in payload
    # Briefing block is the last section.
    assert payload.rstrip().endswith("[/last-briefing]")


def test_envelope_omits_briefing_section_when_no_briefing(tmp_path: Path) -> None:
    """No briefing → no [last-briefing] block."""
    vault = tmp_path
    payload = session_start._build_injection_payload(
        vault, current_project="myproj", inject_briefing=True,
    )
    assert "[last-briefing" not in payload


def test_envelope_omits_briefing_section_when_disabled(tmp_path: Path) -> None:
    """inject_briefing=False suppresses the section even when a briefing exists."""
    vault = tmp_path
    _write_briefing(vault, "myproj", "abc", date="2026-04-19", body="anything")
    payload = session_start._build_injection_payload(
        vault, current_project="myproj", inject_briefing=False,
    )
    assert "[last-briefing" not in payload


def test_envelope_briefing_picked_for_canonical_agent(tmp_path: Path) -> None:
    """The picker reads briefings from the canonical agent dir, not the worktree's."""
    vault = tmp_path
    _write_briefing(vault, "myproj", "real", date="2026-04-19", body="canonical-body")
    payload = session_start._build_injection_payload(
        vault, current_project="myproj", inject_briefing=True,
    )
    assert "canonical-body" in payload
