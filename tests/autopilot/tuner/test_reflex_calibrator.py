"""Tests for reflex calibrator — T7, T8, T9, T10."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from mnemo.autopilot.tuner.reflex_calibrator import (
    CHANGE_PEAK,
    NO_CHANGE_INSUFFICIENT,
    NO_CHANGE_MONOTONE,
    NO_CHANGE_NOISE,
    NO_CHANGE_PINNED,
    CurvePoint,
    ReflexStats,
    ReflexConfig,
    analyze_reflex_log,
    calibrate_thresholds,
    is_pinned,
    pick_from_curve,
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
    with log_path.open("w", encoding="utf-8") as fh:
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
        log_path.write_text('{"project":"p","ts":"' + _ts(0) + '","emitted":[],"silence_reason":null}\n{invalid json}\n', encoding="utf-8")
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

    def test_includes_rows_from_the_rotated_log(self, tmp_path: Path):
        """A project whose rows are all in .jsonl.1 must still be counted."""
        d = tmp_path / ".mnemo"
        d.mkdir(parents=True, exist_ok=True)
        rotated_entries = [
            {"project": "rotated-only", "ts": _ts(1), "emitted": ["x"], "silence_reason": None},
            {"project": "rotated-only", "ts": _ts(2), "emitted": [], "silence_reason": "absolute_floor_fail"},
        ]
        with (d / "reflex-log.jsonl.1").open("w", encoding="utf-8") as fh:
            for e in rotated_entries:
                fh.write(json.dumps(e) + "\n")
        _make_log(tmp_path, [
            {"project": "live-only", "ts": _ts(0), "emitted": ["y"], "silence_reason": None},
        ])
        result = analyze_reflex_log(vault_root=tmp_path)
        assert "rotated-only" in result
        assert result["rotated-only"].total_prompts == 2
        assert result["rotated-only"].emitted_count == 1
        assert "live-only" in result


# ---------------------------------------------------------------------------
# T8 — calibrate_thresholds
# ---------------------------------------------------------------------------

def _pt(value: float, carried: int, *, prompts: int = 500, hindsight: int = 0) -> CurvePoint:
    """One measured point on a carried curve."""
    return CurvePoint(
        value=value, prompts=prompts, fired=carried + hindsight,
        injections=carried + hindsight, carried=carried, hindsight=hindsight,
    )


def _curve(project: str, gap_pairs, floor_pairs, *, prompts: int = 500) -> dict:
    return {project: {
        "relative_gap": [_pt(v, c, prompts=prompts) for v, c in gap_pairs],
        "absolute_floor": [_pt(v, c, prompts=prompts) for v, c in floor_pairs],
    }}


# A curve shaped like the real vault's (#333): carried only falls as the knob
# tightens, so the maximum is at the loose end of the swept range.
_MONOTONE_GAP = [(1.1, 237), (1.15, 224), (1.25, 145), (1.5, 77), (3.0, 7)]
_MONOTONE_FLOOR = [(0.5, 224), (2.0, 224), (3.0, 220), (4.0, 186), (5.0, 152)]


class TestPickFromCurve:
    """The knob moves only for a peak strictly inside the swept range that
    also clears the noise floor at the value in force."""

    def test_monotone_curve_proposes_nothing(self):
        value, reason = pick_from_curve(
            [_pt(v, c) for v, c in _MONOTONE_GAP], current=1.5,
        )
        assert value is None
        assert reason == NO_CHANGE_MONOTONE

    def test_peak_at_the_tight_end_proposes_nothing(self):
        # The mirror image: tightening monotonically "wins". Still an edge,
        # still the bound choosing the number rather than the data.
        rising = [(1.1, 10), (1.15, 20), (1.25, 40), (1.5, 80), (3.0, 160)]
        value, reason = pick_from_curve([_pt(v, c) for v, c in rising], current=1.15)
        assert value is None
        assert reason == NO_CHANGE_MONOTONE

    def test_interior_peak_is_taken(self):
        peaked = [(1.1, 40), (1.15, 60), (1.25, 200), (1.5, 55), (3.0, 10)]
        value, reason = pick_from_curve([_pt(v, c) for v, c in peaked], current=1.5)
        assert value == 1.25
        assert reason == CHANGE_PEAK

    def test_interior_peak_within_noise_of_current_is_refused(self):
        # 82 vs 80 carried out of 500 prompts is inside the Wilson interval.
        peaked = [(1.1, 40), (1.15, 60), (1.25, 82), (1.5, 80), (3.0, 10)]
        value, reason = pick_from_curve([_pt(v, c) for v, c in peaked], current=1.5)
        assert value is None
        assert reason == NO_CHANGE_NOISE

    def test_too_few_points_to_have_an_interior(self):
        value, reason = pick_from_curve([_pt(1.1, 10), _pt(1.5, 20)], current=1.1)
        assert value is None
        assert reason == NO_CHANGE_MONOTONE


class TestCalibrateThresholds:
    def test_monotone_curves_write_nothing(self):
        """The finding that motivated #333: on the real vault every knob is
        monotone, so the calibrator must decline rather than pick a bound."""
        curves = _curve("p", _MONOTONE_GAP, _MONOTONE_FLOOR)
        config, reasons = calibrate_thresholds("p", curves=curves, current=None)
        assert config is None
        assert reasons["relative_gap"] == NO_CHANGE_MONOTONE
        assert reasons["absolute_floor"] == NO_CHANGE_MONOTONE

    def test_never_tightens_a_hand_set_gap_back_toward_the_default(self):
        """#332 pinned this vault at 1.15; the old emit-rate band would have
        walked it back to 1.5. Nothing in the measured curve may do that."""
        current = ReflexConfig(project="p", relative_gap=1.15, absolute_floor=2.0, min_tokens=2)
        curves = _curve("p", _MONOTONE_GAP, _MONOTONE_FLOOR)
        config, _ = calibrate_thresholds("p", curves=curves, current=current)
        assert config is None

    def test_interior_peak_is_written(self):
        peaked = [(1.1, 40), (1.15, 60), (1.25, 300), (1.5, 55), (3.0, 10)]
        curves = _curve("p", peaked, _MONOTONE_FLOOR)
        config, reasons = calibrate_thresholds("p", curves=curves, current=None)
        assert config is not None
        assert config.relative_gap == 1.25
        assert reasons["relative_gap"] == CHANGE_PEAK
        # The untouched knob keeps whatever is in force, it is not reset.
        assert config.absolute_floor == 2.0

    def test_untouched_knob_keeps_the_current_value_not_the_default(self):
        peaked = [(1.1, 40), (1.15, 60), (1.25, 300), (1.5, 55), (3.0, 10)]
        current = ReflexConfig(project="p", relative_gap=1.5, absolute_floor=1.35, min_tokens=2)
        curves = _curve("p", peaked, _MONOTONE_FLOOR)
        config, _ = calibrate_thresholds("p", curves=curves, current=current)
        assert config is not None
        assert config.absolute_floor == 1.35

    def test_pinned_project_is_left_alone_even_with_a_peak(self):
        peaked = [(1.1, 40), (1.15, 60), (1.25, 300), (1.5, 55), (3.0, 10)]
        current = ReflexConfig(
            project="p", relative_gap=1.15, absolute_floor=2.0, min_tokens=2, pinned=True,
        )
        curves = _curve("p", peaked, _MONOTONE_FLOOR)
        config, reasons = calibrate_thresholds("p", curves=curves, current=current)
        assert config is None
        assert reasons["relative_gap"] == NO_CHANGE_PINNED

    def test_returns_none_when_too_few_replayed_prompts(self):
        peaked = [(1.1, 4), (1.15, 6), (1.25, 30), (1.5, 5), (3.0, 1)]
        curves = _curve("p", peaked, _MONOTONE_FLOOR, prompts=40)
        config, reasons = calibrate_thresholds("p", curves=curves, current=None)
        assert config is None
        assert reasons["relative_gap"] == NO_CHANGE_INSUFFICIENT

    def test_unknown_project_returns_none(self):
        config, reasons = calibrate_thresholds("nobody", curves={}, current=None)
        assert config is None
        assert reasons["relative_gap"] == NO_CHANGE_INSUFFICIENT

    def test_project_name_preserved(self):
        peaked = [(1.1, 40), (1.15, 60), (1.25, 300), (1.5, 55), (3.0, 10)]
        curves = _curve("my-project", peaked, _MONOTONE_FLOOR)
        config, _ = calibrate_thresholds("my-project", curves=curves, current=None)
        assert config is not None
        assert config.project == "my-project"

    def test_written_value_stays_inside_the_safe_range(self):
        from mnemo.autopilot.tuner.reflex_calibrator import KNOBS
        peaked = [(1.1, 40), (1.15, 60), (1.25, 300), (1.5, 55), (3.0, 10)]
        curves = _curve("p", peaked, _MONOTONE_FLOOR)
        config, _ = calibrate_thresholds("p", curves=curves, current=None)
        assert config is not None
        for knob, value in (("relative_gap", config.relative_gap),
                            ("absolute_floor", config.absolute_floor)):
            lo, hi = KNOBS[knob][1]
            assert lo <= value <= hi


# ---------------------------------------------------------------------------
# T8b — emit-rate over ELIGIBLE prompts (excludes pre-scoring skips)
# ---------------------------------------------------------------------------

class TestEligibleEmitRate:
    """index_missing / below_min_tokens are pre-scoring skips, not threshold
    rejections — they must NOT dilute emit_rate, else a chatty project hides
    behind a deflated raw rate and never gets tightened."""

    def _stats(self, *, total: int, emitted: int, reasons: dict) -> ReflexStats:
        return ReflexStats(
            project="p",
            total_prompts=total,
            emitted_count=emitted,
            silence_reasons=reasons,
            days_covered=30,
        )

    def test_emit_rate_excludes_dead_end_silences(self):
        # 130 total, 100 of which were never scored (no index / too short).
        # 10 emitted out of 30 eligible = 33%, NOT 10/130 = 7.7%.
        stats = self._stats(
            total=130,
            emitted=10,
            reasons={"index_missing": 80, "below_min_tokens": 20},
        )
        assert stats.eligible_prompts == 30
        assert abs(stats.emit_rate - (10 / 30)) < 1e-9

    def test_threshold_fails_stay_in_denominator(self):
        # absolute_floor_fail / relative_gap_fail ARE scored rejections — keep them.
        stats = self._stats(
            total=100,
            emitted=10,
            reasons={"absolute_floor_fail": 50, "relative_gap_fail": 40},
        )
        assert stats.eligible_prompts == 100
        assert abs(stats.emit_rate - 0.10) < 1e-9

    def test_eligible_rate_exposes_a_deflated_raw_rate(self):
        # The real dogfood bug: raw rate 6% (it used to look "in target") but
        # eligible rate 20%. The rate is descriptive now — #333 — but it still
        # has to be computed over the prompts that were actually scored.
        stats = self._stats(
            total=500,
            emitted=30,  # raw 6%
            reasons={"index_missing": 350},  # eligible = 150
        )
        assert stats.eligible_prompts == 150
        assert abs(stats.emit_rate - 0.20) < 1e-9

    def test_all_exported_silences_do_not_move_the_emit_rate(self):
        # A prompt answered by the exported rules file never reached the
        # gates — it is neither a hit nor a miss for calibration, same as
        # index_missing / below_min_tokens.
        baseline = self._stats(
            total=100,
            emitted=10,
            reasons={"absolute_floor_fail": 90},
        )
        with_exported = self._stats(
            total=600,
            emitted=10,
            reasons={"absolute_floor_fail": 90, "all_exported": 500},
        )
        assert with_exported.eligible_prompts == baseline.eligible_prompts == 100
        assert abs(with_exported.emit_rate - baseline.emit_rate) < 1e-9


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
        data = json.loads(path.read_text(encoding="utf-8"))
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
        from mnemo.autopilot.core.kill_switch import set_state
        set_state(vault_root=tmp_path, state="off")

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


# ---------------------------------------------------------------------------
# #333 — the curve is measured through the hook's own decision
# ---------------------------------------------------------------------------

class TestCarriedCurves:
    """`carried_curves` must replay the real `decide`, not a model of it."""

    def _vault(self, tmp_path: Path):
        """A rule taught in session A, asked again in a later session B."""
        from datetime import datetime, timedelta, timezone

        t0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        vault = tmp_path / "vault"
        sid_a = "aaaaaaaa-0000-0000-0000-000000000001"
        sid_b = "bbbbbbbb-0000-0000-0000-000000000002"

        briefing = vault / "bots" / "alpha" / "briefings" / "sessions" / f"{sid_a}.md"
        briefing.parent.mkdir(parents=True, exist_ok=True)
        briefing.write_text("# briefing\n", encoding="utf-8")
        src = f"bots/alpha/briefings/sessions/{sid_a}.md"

        d = vault / "shared" / "feedback"
        d.mkdir(parents=True, exist_ok=True)
        learned = (t0 + timedelta(hours=1)).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
        (d / "use-prisma-mock.md").write_text(
            "---\nname: use-prisma-mock\n"
            "description: Always use jest-mock-extended to mock Prisma in tests\n"
            f"type: feedback\nextracted_at: {learned}\nstability: stable\n"
            "tags:\n  - prisma\n  - testing\n"
            f"sources:\n  - {src}\n---\n"
            "Mock the Prisma client in tests using jest-mock-extended.\n",
            encoding="utf-8",
        )
        for i, (name, desc, tag) in enumerate([
            ("use-yarn", "Prefer yarn over npm for installs", "yarn"),
            ("commit-strategy", "Small atomic commits with clear messages", "git"),
            ("python-style", "Follow PEP8 and black formatting", "python"),
        ]):
            (d / f"{name}.md").write_text(
                f"---\nname: {name}\ndescription: {desc}\ntype: feedback\n"
                "extracted_at: 2026-01-01T00:00:00\nstability: stable\n"
                f"tags:\n  - {tag}\nsources:\n  - bots/noise{i}/memory/x.md\n---\n"
                f"Body for {name}.\n",
                encoding="utf-8",
            )
        return vault, sid_b, (t0 + timedelta(days=2)).timestamp()

    def test_curve_is_built_per_knob_and_carries_at_a_loose_gap(self, tmp_path: Path):
        from mnemo.autopilot.tuner.reflex_calibrator import FLOOR_CANDIDATES, GAP_CANDIDATES, carried_curves
        from mnemo.core.reflex import replay as rp
        from mnemo.core.reflex.index import build_index

        vault, sid_b, ts = self._vault(tmp_path)
        prompts = [rp.Prompt(
            session_id=sid_b, project="alpha", ts=ts,
            text="How do I mock prisma in a jest test with typescript",
        )]
        curves = carried_curves(
            vault_root=vault, prompts=prompts,
            index=build_index(vault), facts=rp.rule_facts(vault),
            reflex_cfg={"enabled": True, "maxEmissionsPerSession": 10},
        )

        assert set(curves) == {"alpha"}
        assert [p.value for p in curves["alpha"]["relative_gap"]] == list(GAP_CANDIDATES)
        assert [p.value for p in curves["alpha"]["absolute_floor"]] == list(FLOOR_CANDIDATES)
        assert all(p.prompts == 1 for p in curves["alpha"]["relative_gap"])

        # The rule was learned in an earlier session → carried, not hindsight.
        loose = curves["alpha"]["relative_gap"][0]
        assert loose.carried == 1 and loose.hindsight == 0

    def test_tightening_the_floor_walks_carried_down_to_zero(self, tmp_path: Path):
        from mnemo.autopilot.tuner.reflex_calibrator import carried_curves
        from mnemo.core.reflex import replay as rp
        from mnemo.core.reflex.index import build_index

        vault, sid_b, ts = self._vault(tmp_path)
        prompts = [rp.Prompt(
            session_id=sid_b, project="alpha", ts=ts,
            text="How do I mock prisma in a jest test with typescript",
        )]
        curves = carried_curves(
            vault_root=vault, prompts=prompts,
            index=build_index(vault), facts=rp.rule_facts(vault),
            reflex_cfg={"enabled": True, "maxEmissionsPerSession": 10},
        )
        floor = curves["alpha"]["absolute_floor"]
        carried = [p.carried for p in floor]
        assert carried[0] == 1 and carried[-1] == 0
        # Monotone non-increasing — this is the shape #333 measured on the vault.
        assert all(a >= b for a, b in zip(carried, carried[1:]))

    def test_no_prompts_yields_no_curves(self, tmp_path: Path):
        from mnemo.autopilot.tuner.reflex_calibrator import carried_curves
        assert carried_curves(vault_root=tmp_path, prompts=[], reflex_cfg={}) == {}


# ---------------------------------------------------------------------------
# #333 — a hand-set threshold outranks any measurement
# ---------------------------------------------------------------------------

class TestPinned:
    def _write(self, tmp_path: Path, **kw) -> Path:
        d = tmp_path / ".mnemo"
        d.mkdir(parents=True, exist_ok=True)
        path = d / "reflex-config.p.json"
        payload = {"project": "p", "relative_gap": 1.15, "absolute_floor": 2.0, "min_tokens": 2}
        payload.update(kw)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_pinned_round_trips(self, tmp_path: Path):
        self._write(tmp_path, pinned=True)
        loaded = load_reflex_config("p", tmp_path)
        assert loaded is not None and loaded.pinned is True
        assert is_pinned("p", tmp_path)

    def test_absent_pin_key_is_not_pinned(self, tmp_path: Path):
        self._write(tmp_path)
        assert not is_pinned("p", tmp_path)

    def test_missing_file_is_not_pinned(self, tmp_path: Path):
        assert not is_pinned("nobody", tmp_path)

    def test_write_refuses_to_overwrite_a_pinned_file(self, tmp_path: Path):
        path = self._write(tmp_path, pinned=True)
        before = path.read_text(encoding="utf-8")
        proposal = ReflexConfig(project="p", relative_gap=1.5, absolute_floor=2.0, min_tokens=2)
        assert write_reflex_config(proposal, tmp_path) is False
        assert path.read_text(encoding="utf-8") == before

    def test_a_pinned_config_can_still_be_written_by_hand(self, tmp_path: Path):
        self._write(tmp_path, pinned=True)
        pinned = ReflexConfig(
            project="p", relative_gap=1.2, absolute_floor=2.0, min_tokens=2, pinned=True,
        )
        assert write_reflex_config(pinned, tmp_path) is True
        assert load_reflex_config("p", tmp_path).relative_gap == 1.2

    def test_pin_key_survives_the_write_and_is_ignored_by_the_gate_reader(self, tmp_path: Path):
        from mnemo.core.reflex.project_config import load_project_thresholds
        self._write(tmp_path, pinned=True)
        overrides = load_project_thresholds(tmp_path, "p")
        assert overrides == {"relative_gap": 1.15, "absolute_floor": 2.0, "term_overlap_min": 2}

    def test_open_pr_leaves_a_pinned_project_alone(self, tmp_path: Path, capsys):
        from mnemo.autopilot.core.kill_switch import set_state
        set_state(vault_root=tmp_path, state="on", source="test")
        path = self._write(tmp_path, pinned=True)
        before = path.read_text(encoding="utf-8")
        proposal = ReflexConfig(project="p", relative_gap=1.5, absolute_floor=2.0, min_tokens=2)

        rc = open_reflex_calibration_pr({"p": proposal}, vault_root=tmp_path, dry_run=False)

        assert path.read_text(encoding="utf-8") == before
        assert rc == -1
        assert "pinned" in capsys.readouterr().out
