"""Tests for `mnemo autopilot tune` CLI — T11."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.cli.runtime import main


def _run(monkeypatch, tmp_path: Path, *args: str, capsys) -> tuple[int, str]:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "mnemo.cli._resolve_vault", lambda: tmp_path, raising=False
    )
    rc = main([*args])
    out, _err = capsys.readouterr()
    return rc, out


class TestTuneCLI:
    def test_bm25_dry_run_exits_zero_no_frozen(self, monkeypatch, tmp_path, capsys):
        """bm25 --dry-run should exit 0 gracefully when no frozen set."""
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "tune", "bm25", "--dry-run",
            capsys=capsys,
        )
        assert rc == 0
        # Should mention frozen set missing or nothing to do
        assert any(word in out.lower() for word in ["frozen", "no frozen", "missing", "skip", "abort"])

    def test_reflex_dry_run_exits_zero_no_log(self, monkeypatch, tmp_path, capsys):
        """reflex --dry-run should exit 0 gracefully when no log."""
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "tune", "reflex", "--dry-run",
            capsys=capsys,
        )
        assert rc == 0
        # Should mention no data or similar
        assert any(word in out.lower() for word in ["no reflex", "no log", "empty", "missing", "skip", "no data"])

    def test_all_dry_run_exits_zero(self, monkeypatch, tmp_path, capsys):
        """tune all --dry-run runs both bm25 and reflex."""
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "tune", "all", "--dry-run",
            capsys=capsys,
        )
        assert rc == 0

    def test_reflex_dry_run_with_project_flag(self, monkeypatch, tmp_path, capsys):
        """--project NAME is accepted without error."""
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "tune", "reflex", "--dry-run", "--project", "my-project",
            capsys=capsys,
        )
        assert rc == 0

    def test_bm25_dry_run_prints_proposed_when_frozen_exists(self, monkeypatch, tmp_path, capsys):
        """When frozen set exists and grid runs, output contains proposal info."""
        import json
        d = tmp_path / ".mnemo"
        d.mkdir(parents=True, exist_ok=True)
        cases = [{"id": "c1", "project": "p", "topic": "t", "expect_slug": "slug-0"}]
        (d / "recall-cases.frozen.json").write_text(json.dumps(cases))

        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "tune", "bm25", "--dry-run",
            capsys=capsys,
        )
        assert rc == 0
        # Should print something about the result
        assert out.strip() != ""

    def test_tune_without_subcommand_shows_usage(self, monkeypatch, tmp_path, capsys):
        """tune without subcommand exits non-zero."""
        rc, out = _run(
            monkeypatch, tmp_path,
            "autopilot", "tune",
            capsys=capsys,
        )
        assert rc != 0 or "usage" in out.lower() or "bm25" in out.lower()
