from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.cli.runtime import main


def _run(monkeypatch, tmp_path: Path, *args: str, capsys) -> tuple:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path, raising=False)
    rc = main([*args])
    out, err = capsys.readouterr()
    return rc, out, err


# ── mnemo autopilot digest ────────────────────────────────────────────────────

def test_digest_basic(monkeypatch, tmp_path, capsys):
    rc, out, _ = _run(monkeypatch, tmp_path, "autopilot", "digest", capsys=capsys)
    assert rc == 0
    # Should print the path to the generated file
    assert "digest.md" in out
    # File should exist
    path = tmp_path / "briefings" / "autopilot"
    md_files = list(path.glob("*-digest.md"))
    assert len(md_files) == 1


def test_digest_since_30d(monkeypatch, tmp_path, capsys):
    rc, out, _ = _run(
        monkeypatch, tmp_path, "autopilot", "digest", "--since", "30d",
        capsys=capsys,
    )
    assert rc == 0
    assert "digest.md" in out


def test_digest_post_calls_issue_creator(monkeypatch, tmp_path, capsys):
    posted = []

    def fake_post_digest_issue(*, digest, _run=None):
        posted.append(digest)
        return 99

    monkeypatch.setattr(
        "mnemo.autopilot.insights.digest.post_digest_issue",
        fake_post_digest_issue,
    )
    rc, out, _ = _run(
        monkeypatch, tmp_path, "autopilot", "digest", "--post",
        capsys=capsys,
    )
    assert rc == 0
    assert len(posted) == 1
    assert "99" in out or "issue" in out.lower()


def test_digest_post_no_issue_created(monkeypatch, tmp_path, capsys):
    """When gh returns None, CLI should still exit 0."""
    monkeypatch.setattr(
        "mnemo.autopilot.insights.digest.post_digest_issue",
        lambda *, digest, _run=None: None,
    )
    rc, out, _ = _run(
        monkeypatch, tmp_path, "autopilot", "digest", "--post",
        capsys=capsys,
    )
    assert rc == 0


# ── mnemo autopilot collect-misses ───────────────────────────────────────────

def test_collect_misses_no_report(monkeypatch, tmp_path, capsys):
    rc, out, _ = _run(
        monkeypatch, tmp_path, "autopilot", "collect-misses",
        capsys=capsys,
    )
    assert rc == 0
    assert "0" in out


def test_collect_misses_with_misses(monkeypatch, tmp_path, capsys):
    # Write a synthetic recall-report with 2 misses
    import json as _json
    data = {
        "generated_at": "2026-04-30T10:00:00Z",
        "report": {"cases": 2, "misses": ["m1", "m2"]},
        "results": [
            {"id": "c1", "hit": False, "rank": None, "expect_slug": "miss-a",
             "topic": "foo", "project": "p", "result_count": 5, "elapsed_ms": 1.0},
            {"id": "c2", "hit": False, "rank": None, "expect_slug": "miss-b",
             "topic": "bar", "project": "p", "result_count": 5, "elapsed_ms": 1.0},
        ],
    }
    (tmp_path / ".mnemo").mkdir(exist_ok=True)
    (tmp_path / ".mnemo" / "recall-report.json").write_text(_json.dumps(data))

    rc, out, _ = _run(
        monkeypatch, tmp_path, "autopilot", "collect-misses",
        capsys=capsys,
    )
    assert rc == 0
    assert "2" in out


# ── autopilot on activates state for hook-driven scheduling ──────────────────

def test_autopilot_on_activates_state(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "mnemo.autopilot.core.labels.ensure_label_exists",
        lambda: None,
        raising=False,
    )
    rc, _, _ = _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    assert rc == 0

    state_path = tmp_path / ".mnemo" / "autopilot.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert data["state"] == "on"
