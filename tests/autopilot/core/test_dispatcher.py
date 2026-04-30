import json
from pathlib import Path

import pytest

from mnemo.autopilot.core.dispatcher import (
    schedule_autopilot_job,
    list_autopilot_jobs,
    cancel_autopilot_job,
)


def test_schedule_records_job(tmp_path: Path):
    h = schedule_autopilot_job(
        vault_root=tmp_path,
        name="autopilot.tier0.digest",
        cron="0 9 * * 1",
        command="mnemo autopilot digest",
    )
    assert h.name == "autopilot.tier0.digest"
    assert h.cron == "0 9 * * 1"

    data = json.loads((tmp_path / ".mnemo" / "autopilot-jobs.json").read_text())
    assert "autopilot.tier0.digest" in data["jobs"]


def test_schedule_namespaces_must_start_with_autopilot(tmp_path: Path):
    with pytest.raises(ValueError, match="autopilot\\."):
        schedule_autopilot_job(
            vault_root=tmp_path, name="random.job",
            cron="* * * * *", command="x",
        )


def test_list_jobs_empty(tmp_path: Path):
    assert list_autopilot_jobs(vault_root=tmp_path) == []


def test_list_jobs_returns_registered(tmp_path: Path):
    schedule_autopilot_job(
        vault_root=tmp_path, name="autopilot.tier1.selffix",
        cron="0 8 * * *", command="mnemo autopilot self-fix",
    )
    jobs = list_autopilot_jobs(vault_root=tmp_path)
    assert len(jobs) == 1
    assert jobs[0].name == "autopilot.tier1.selffix"


def test_cancel_removes_job(tmp_path: Path):
    schedule_autopilot_job(
        vault_root=tmp_path, name="autopilot.tier1.selffix",
        cron="0 8 * * *", command="x",
    )
    assert cancel_autopilot_job(vault_root=tmp_path, name="autopilot.tier1.selffix") is True
    assert list_autopilot_jobs(vault_root=tmp_path) == []
    assert cancel_autopilot_job(vault_root=tmp_path, name="autopilot.tier1.selffix") is False


def test_schedule_is_idempotent_on_same_name(tmp_path: Path):
    schedule_autopilot_job(
        vault_root=tmp_path, name="autopilot.tier0.digest",
        cron="0 9 * * 1", command="cmd-v1",
    )
    schedule_autopilot_job(
        vault_root=tmp_path, name="autopilot.tier0.digest",
        cron="0 10 * * 1", command="cmd-v2",
    )
    jobs = list_autopilot_jobs(vault_root=tmp_path)
    assert len(jobs) == 1
    assert jobs[0].cron == "0 10 * * 1"
    assert jobs[0].command == "cmd-v2"
