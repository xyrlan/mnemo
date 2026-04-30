"""Tests for reflex calibrator — T7, T8, T9, T10."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from mnemo.autopilot.tuner.reflex_calibrator import (
    ReflexStats,
    ReflexConfig,
    analyze_reflex_log,
    calibrate_thresholds,
    write_reflex_config,
    load_reflex_config,
    open_reflex_calibration_pr,
)


def _ts(days_ago: float = 0.0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_log(vault_root: Path, entries: list[dict]) -> Path:
    d = vault_root / ".mnemo"
    d.mkdir(parents=True, exist_ok=True)
    log_path = d / "reflex-log.jsonl"
    with log_path.open("w") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return log_path


# ---------------------------------------------------------------------------
# T7 — analyze_reflex_log
# ---------------------------------------------------------------------------

class TestAnalyzeReflexLog:
    def test_missing_log_returns_empty(self, tmp_path: Path):
        result = analyze_reflex_log(vault_root=tmp_path)
        assert result == {}

    def test_parses_emitted_entries(self, tmp_path: Path):
        entries = [
            {"project": "proj-a", "ts": _ts(0), "emitted": ["slug-1"], "silence_reason": None},
            {"project": "proj-a", "ts": _ts(1), "emitted": ["slug-2"], "silence_reason": None},
            {"project": "proj-a", "ts": _ts(2), "emitted": [], "silence_reason": "absolute_floor_fail"},
        ]
        _make_log(tmp_path, entries)
        result = analyze_reflex_log(vault_root=tmp_path)
        assert "proj-a" in result
        stats = result["proj-a"]
        assert stats.total_prompts == 3
        assert stats.emitted_count == 2

    def test_filters_by_window(self, tmp_path: Path):
        entries = [
            {"project": "proj-a", "ts": _ts(0), "emitted": ["slug-1"], "silence_reason": None},
            {"project": "proj-a", "ts": _ts(35), "emitted": ["slug-old"], "silence_reason": None},  # outside window
        ]
        _make_log(tmp_path, entries)
        result = analyze_reflex_log(vault_root=tmp_path, window_days=30)
        stats = result.get("proj-a")
        assert stats is not None
        assert stats.total_prompts == 1

    def test_project_filter(self, tmp_path: Path):
        entries = [
            {"project": "proj-a", "ts": _ts(0), "emitted": [], "silence_reason": "absolute_floor_fail"},
            {"project": "proj-b", "ts": _ts(0), "emitted": ["x"], "silence_reason": None},
        ]
        _make_log(tmp_path, entries)
        result = analyze_reflex_log(vault_root=tmp_path, project="proj-a")
        assert "proj-a" in result
        assert "proj-b" not in result

    def test_silence_reason_breakdown(self, tmp_path: Path):
        entries = [
            {"project": "p", "ts": _ts(0), "emitted": [], "silence_reason": "absolute_floor_fail"},
            {"project": "p", "ts": _ts(0), "emitted": [], "silence_reason": "relative_gap_fail"},
            {"project": "p", "ts": _ts(0), "emitted": [], "silence_reason": "absolute_floor_fail"},
        ]
        _make_log(tmp_path, entries)
        result = analyze_reflex_log(vault_root=tmp_path)
        stats = result["p"]
        assert stats.silence_reasons["absolute_floor_fail"] == 2
        assert stats.silence_reasons["relative_gap_fail"] == 1

    def test_skips_invalid_json_lines(self, tmp_path: Path):
        d = tmp_path / ".mnemo"
        d.mkdir(parents=True, exist_ok=True)
        log_path = d / "reflex-log.jsonl"
        log_path.write_text('{"project":"p","ts":"' + _ts(0) + '","emitted":[],"silence_reason":null}\n{invalid json}\n')
        result = analyze_reflex_log(vault_root=tmp_path)
        assert "p" in result
        assert result["p"].total_prompts == 1

    def test_aggregate_all_projects_when_no_filter(self, tmp_path: Path):
        entries = [
            {"project": "a", "ts": _ts(0), "emitted": ["x"], "silence_reason": None},
            {"project": "b", "ts": _ts(0), "emitted": [], "silence_reason": "term_overlap_fail"},
        ]
        _make_log(tmp_path, entries)
        result = analyze_reflex_log(vault_root=tmp_path)
        assert "a" in result
        assert "b" in result


# ---------------------------------------------------------------------------
# T8 — calibrate_thresholds
# ---------------------------------------------------------------------------

class TestCalibrateThresholds:
    def _stats(self, total: int, emitted: int, project: str = "p") -> ReflexStats:
        return ReflexStats(
            project=project,
            total_prompts=total,
            emitted_count=emitted,
            silence_reasons={},
            days_covered=30,
        )

    def test_returns_none_when_insufficient_data(self):
        stats = self._stats(50, 5)
        result = calibrate_thresholds(stats)
        assert result is None

    def test_returns_config_when_sufficient(self):
        stats = self._stats(200, 14)  # 7% emit rate — within [3%, 12%]
        result = calibrate_thresholds(stats)
        assert result is not None
        assert isinstance(result, ReflexConfig)

    def test_predicted_rate_in_target_range_when_current_ok(self):
        # Current rate = 7% — already in target, thresholds should be close to default
        stats = self._stats(200, 14)  # 7%
        config = calibrate_thresholds(stats)
        assert config is not None
        # predicted rate should be in [3%, 12%]
        from mnemo.core.reflex.gates import DEFAULT_THRESHOLDS
        # Just check config fields are in valid range
        assert 1.0 <= config.relative_gap <= 3.5
        assert 0.5 <= config.absolute_floor <= 6.0

    def test_lowers_thresholds_when_rate_too_low(self):
        # Only 1% emit rate → need to loosen thresholds
        stats = self._stats(500, 5)  # 1%
        config = calibrate_thresholds(stats)
        assert config is not None
        from mnemo.autopilot.tuner.reflex_calibrator import DEFAULT_REFLEX_CONFIG
        # Loosened thresholds should be lower than defaults
        assert config.relative_gap <= DEFAULT_REFLEX_CONFIG.relative_gap
        assert config.absolute_floor <= DEFAULT_REFLEX_CONFIG.absolute_floor

    def test_raises_thresholds_when_rate_too_high(self):
        # 20% emit rate → need to tighten thresholds
        stats = self._stats(500, 100)  # 20%
        config = calibrate_thresholds(stats)
        assert config is not None
        from mnemo.autopilot.tuner.reflex_calibrator import DEFAULT_REFLEX_CONFIG
        # Tightened thresholds should be >= defaults
        assert config.relative_gap >= DEFAULT_REFLEX_CONFIG.relative_gap
        assert config.absolute_floor >= DEFAULT_REFLEX_CONFIG.absolute_floor

    def test_project_name_preserved(self):
        stats = self._stats(200, 14, project="my-project")
        config = calibrate_thresholds(stats)
        assert config is not None
        assert config.project == "my-project"

    def test_clamps_to_safe_range(self):
        # Extreme low rate
        stats = self._stats(10000, 0)  # 0%
        config = calibrate_thresholds(stats)
        assert config is not None
        assert config.relative_gap >= 1.1  # safe floor
        assert config.absolute_floor >= 0.5  # safe floor

    def test_clamps_upper_bound(self):
        # Extreme high rate
        stats = self._stats(10000, 9999)  # ~100%
        config = calibrate_thresholds(stats)
        assert config is not None
        assert config.relative_gap <= 3.0  # safe ceiling
        assert config.absolute_floor <= 5.0  # safe ceiling


# ---------------------------------------------------------------------------
# T9 — reflex config JSON I/O
# ---------------------------------------------------------------------------

class TestReflexConfigIO:
    def test_write_and_load_round_trip(self, tmp_path: Path):
        config = ReflexConfig(
            project="my-project",
            relative_gap=1.8,
            absolute_floor=3.0,
            min_tokens=3,
        )
        write_reflex_config(config, tmp_path)
        loaded = load_reflex_config("my-project", tmp_path)
        assert loaded is not None
        assert loaded.project == config.project
        assert loaded.relative_gap == config.relative_gap
        assert loaded.absolute_floor == config.absolute_floor
        assert loaded.min_tokens == config.min_tokens

    def test_load_returns_none_when_missing(self, tmp_path: Path):
        result = load_reflex_config("nonexistent-proj", tmp_path)
        assert result is None

    def test_creates_mnemo_dir(self, tmp_path: Path):
        config = ReflexConfig(project="p", relative_gap=1.5, absolute_floor=2.0, min_tokens=2)
        write_reflex_config(config, tmp_path)
        expected = tmp_path / ".mnemo" / "reflex-config.p.json"
        assert expected.exists()

    def test_written_file_is_valid_json(self, tmp_path: Path):
        config = ReflexConfig(project="test", relative_gap=1.5, absolute_floor=2.0, min_tokens=2)
        write_reflex_config(config, tmp_path)
        path = tmp_path / ".mnemo" / "reflex-config.test.json"
        data = json.loads(path.read_text())
        assert "project" in data
        assert "relative_gap" in data


# ---------------------------------------------------------------------------
# T10 — open_reflex_calibration_pr
# ---------------------------------------------------------------------------

class TestOpenReflexCalibrationPR:
    def _good_config(self, project: str) -> ReflexConfig:
        return ReflexConfig(project=project, relative_gap=1.5, absolute_floor=2.0, min_tokens=2)

    def test_dry_run_returns_minus_one(self, tmp_path: Path):
        configs = {"p": self._good_config("p")}
        result = open_reflex_calibration_pr(configs, vault_root=tmp_path, dry_run=True)
        assert result == -1

    def test_dry_run_does_not_write_files(self, tmp_path: Path):
        configs = {"p": self._good_config("p")}
        open_reflex_calibration_pr(configs, vault_root=tmp_path, dry_run=True)
        assert not (tmp_path / ".mnemo" / "reflex-config.p.json").exists()

    def test_dry_run_prints_config(self, tmp_path: Path, capsys):
        configs = {"myproj": self._good_config("myproj")}
        open_reflex_calibration_pr(configs, vault_root=tmp_path, dry_run=True)
        captured = capsys.readouterr()
        assert "myproj" in captured.out

    def test_skips_none_configs(self, tmp_path: Path):
        """None configs (insufficient data) should be filtered."""
        configs = {"p": None}  # type: ignore[dict-item]
        result = open_reflex_calibration_pr(configs, vault_root=tmp_path, dry_run=True)
        # With no valid configs, returns -1 for dry_run
        assert result == -1

    def test_skips_when_kill_switch_off(self, tmp_path: Path):
        configs = {"p": self._good_config("p")}
        result = open_reflex_calibration_pr(configs, vault_root=tmp_path, dry_run=False)
        assert result == -2  # skipped due to budget/kill switch

    def test_non_dry_run_writes_configs_when_active(self, tmp_path: Path):
        from mnemo.autopilot.core.kill_switch import set_state
        set_state(vault_root=tmp_path, state="on")

        configs = {"p": self._good_config("p")}
        result = open_reflex_calibration_pr(configs, vault_root=tmp_path, dry_run=False)
        assert result == 0
        assert (tmp_path / ".mnemo" / "reflex-config.p.json").exists()
