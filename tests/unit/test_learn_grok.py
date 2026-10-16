"""``mnemo learn`` picks the session of the CLI it runs under; Claude's pick is unchanged."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mnemo.core import learn as learn_mod
from mnemo.core.backfill import discover
from mnemo.core.grok import paths as grok_paths
from tests.unit._grok_history import SESSION_ID, write_session

REPO = "/Users/you/github/app"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    for name in ("GROK_HOME", "GROK_SESSION_ID", "MNEMO_AGENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(discover, "agent_for_cwd", lambda cwd: "app")


@pytest.fixture
def both(tmp_home: Path):
    """A Claude transcript and a newer Grok session in the same project."""
    claude = tmp_home / ".claude" / "projects" / discover.encode_cwd(REPO) / "c1.jsonl"
    claude.parent.mkdir(parents=True)
    claude.write_text(json.dumps({"type": "user", "cwd": REPO}) + "\n", encoding="utf-8")
    os.utime(claude, (1_000, 1_000))
    grok = write_session(tmp_home / ".grok", cwd=REPO)
    os.utime(grok, (9_000, 9_000))
    return claude, grok


def test_a_claude_process_still_learns_from_claudes_newest(both):
    claude, _grok = both

    assert learn_mod.newest_transcript(REPO) == claude


def test_inside_a_grok_session_it_learns_from_groks_newest(both, monkeypatch):
    _claude, grok = both
    monkeypatch.setenv("GROK_SESSION_ID", SESSION_ID)

    assert learn_mod.newest_transcript(REPO) == grok


def test_mnemo_agent_grok_picks_grok_too(both, monkeypatch):
    monkeypatch.setenv("MNEMO_AGENT", "grok")

    assert learn_mod.newest_transcript(REPO) == both[1]


def test_a_grok_session_is_found_by_id_or_short_id_from_either_cli(both):
    claude, grok = both

    assert learn_mod.newest_transcript(REPO, session_id=SESSION_ID) == grok
    assert learn_mod.newest_transcript(REPO, session_id=grok_paths.short_id(SESSION_ID)) == grok
    assert learn_mod.newest_transcript(REPO, session_id="c1") == claude
    assert learn_mod.newest_transcript(REPO, session_id="chat_history") is None


def test_no_grok_session_says_so_in_grok_terms(tmp_home: Path, monkeypatch):
    monkeypatch.setenv("MNEMO_AGENT", "grok")

    report = learn_mod.learn({}, cwd=REPO)

    assert report.error == learn_mod.NO_GROK_TRANSCRIPT


def test_an_unknown_agent_is_reported_not_raised(tmp_home: Path, monkeypatch):
    monkeypatch.setenv("MNEMO_AGENT", "gemini")

    report = learn_mod.learn({}, cwd=REPO)

    assert "unknown agent CLI 'gemini'" in report.error


def test_learn_scopes_extraction_to_the_grok_briefing(both, monkeypatch, tmp_path: Path):
    _claude, grok = both
    monkeypatch.setenv("GROK_SESSION_ID", SESSION_ID)
    seen = {}

    def fake_briefing(path, project, cfg, **kw):
        seen["path"] = path
        out = tmp_path / f"{SESSION_ID}.md"
        out.write_text("---\ncorrections: 0\n---\n", encoding="utf-8")
        return out

    def fake_extraction(cfg, *, only):
        seen["only"] = only
        return None

    monkeypatch.setattr("mnemo.core.briefing.generate_session_briefing", fake_briefing)
    monkeypatch.setattr("mnemo.core.extract.run_extraction", fake_extraction)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_path)
    monkeypatch.setattr("mnemo.core.learned.max_seq", lambda root: 0)
    monkeypatch.setattr("mnemo.core.learned._read", lambda root: [])

    report = learn_mod.learn({}, cwd=REPO)

    assert report.error == "" and report.transcript == grok
    assert seen["path"] == grok
    assert seen["only"] == f"feedback/briefing-{SESSION_ID}"
