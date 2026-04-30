from pathlib import Path

from mnemo.autopilot.core._dirs import (
    autopilot_dir,
    proposals_dir,
    autopilot_state_path,
    autopilot_budget_path,
    autopilot_jobs_path,
    frozen_recall_path,
)


def test_autopilot_dir_is_under_vault_mnemo(tmp_path: Path):
    assert autopilot_dir(tmp_path) == tmp_path / ".mnemo"


def test_paths_are_namespaced(tmp_path: Path):
    assert proposals_dir(tmp_path) == tmp_path / ".mnemo" / "proposals"
    assert autopilot_state_path(tmp_path) == tmp_path / ".mnemo" / "autopilot.json"
    assert autopilot_budget_path(tmp_path) == tmp_path / ".mnemo" / "autopilot-budget.json"
    assert autopilot_jobs_path(tmp_path) == tmp_path / ".mnemo" / "autopilot-jobs.json"
    assert frozen_recall_path(tmp_path) == tmp_path / ".mnemo" / "recall-cases.frozen.json"


def test_proposals_dir_is_created_on_demand(tmp_path: Path):
    from mnemo.autopilot.core._dirs import ensure_proposals_dir
    p = ensure_proposals_dir(tmp_path)
    assert p.exists() and p.is_dir()
