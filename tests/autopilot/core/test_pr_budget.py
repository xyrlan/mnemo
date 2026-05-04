import json
from pathlib import Path

import pytest

from mnemo.autopilot.core.pr_budget import (
    can_open,
    record_opened,
    record_outcome,
)
from mnemo.autopilot.core.kill_switch import get_state, set_state


def test_default_budget_allows_first_pr(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    ok, reason = can_open(vault_root=tmp_path, category="doctor_self_fix")
    assert ok is True
    assert reason == ""


def test_budget_blocks_when_kill_switch_off(tmp_path: Path):
    set_state(vault_root=tmp_path, state="off")
    ok, reason = can_open(vault_root=tmp_path, category="doctor_self_fix")
    assert ok is False
    assert "kill switch" in reason.lower() or "off" in reason.lower()


def test_budget_blocks_after_daily_cap(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=10)
    ok, reason = can_open(vault_root=tmp_path, category="doctor_self_fix")
    assert ok is False
    assert "daily" in reason.lower() or "cap" in reason.lower()


def test_budget_categories_are_independent(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=10)
    ok, _ = can_open(vault_root=tmp_path, category="dead_rule_sweep")
    assert ok is True


def test_two_closed_in_a_row_pauses_autopilot(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=10)
    record_outcome(vault_root=tmp_path, pr_number=10, outcome="closed")
    # still on
    assert get_state(vault_root=tmp_path) == "on"
    # second closed across a different day or category triggers the trip
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=11)
    record_outcome(vault_root=tmp_path, pr_number=11, outcome="closed")
    assert get_state(vault_root=tmp_path) == "paused"


def test_merged_outcome_resets_streak(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=10)
    record_outcome(vault_root=tmp_path, pr_number=10, outcome="closed")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=11)
    record_outcome(vault_root=tmp_path, pr_number=11, outcome="merged")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=12)
    record_outcome(vault_root=tmp_path, pr_number=12, outcome="closed")
    assert get_state(vault_root=tmp_path) == "on"


def test_window_rolls_over_after_utc_day(tmp_path: Path, monkeypatch):
    set_state(vault_root=tmp_path, state="on")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=10)

    # roll the window manually to a previous day
    from mnemo.autopilot.core._dirs import autopilot_budget_path
    p = autopilot_budget_path(tmp_path)
    data = json.loads(p.read_text())
    data["window_start"] = "2000-01-01T00:00:00Z"
    p.write_text(json.dumps(data))

    ok, _ = can_open(vault_root=tmp_path, category="doctor_self_fix")
    assert ok is True
