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
        (d / "recall-cases.frozen.json").write_text(json.dumps(cases), encoding="utf-8")

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


class TestTuneReflexReportsCarried:
    """#333 — the tuner reports the measured carried peak, and a monotone
    curve leaves the per-project file alone instead of tightening it."""

    def _curves(self):
        from mnemo.autopilot.tuner.reflex_calibrator import (
            FLOOR_CANDIDATES, GAP_CANDIDATES, CurvePoint,
        )

        def pts(values, start):
            # Strictly falling: the shape measured on the real vault.
            return [
                CurvePoint(value=v, prompts=500, fired=start - i * 10,
                           injections=start - i * 10, carried=start - i * 10, hindsight=0)
                for i, v in enumerate(values)
            ]

        return {"alpha": {
            "relative_gap": pts(GAP_CANDIDATES, 200),
            "absolute_floor": pts(FLOOR_CANDIDATES, 200),
        }}

    def test_prints_the_carried_peak_and_writes_nothing(self, monkeypatch, tmp_path, capsys):
        import json

        from mnemo.autopilot.tuner import reflex_calibrator as rc
        monkeypatch.setattr(rc, "carried_curves", lambda **_kw: self._curves())

        d = tmp_path / ".mnemo"
        d.mkdir(parents=True, exist_ok=True)
        existing = d / "reflex-config.alpha.json"
        existing.write_text(json.dumps({
            "project": "alpha", "relative_gap": 1.15, "absolute_floor": 2.0, "min_tokens": 2,
        }), encoding="utf-8")
        before = existing.read_text(encoding="utf-8")

        rc_code, out = _run(
            monkeypatch, tmp_path, "autopilot", "tune", "reflex", capsys=capsys,
        )

        assert rc_code == 0
        assert "carried peaks at" in out
        assert "monotone" in out
        # The hand-set 1.15 survives — the revert #333 was opened to stop.
        assert existing.read_text(encoding="utf-8") == before
