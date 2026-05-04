from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mnemo.autopilot.core.kill_switch import set_state
from mnemo.autopilot.core.scheduler import run_due_jobs, status_summary
from mnemo.autopilot.core.triggers import last_run, mark_run, runs_path


def test_run_due_noop_when_kill_switch_off(tmp_path: Path):
    set_state(vault_root=tmp_path, state="off")
    out = run_due_jobs(vault_root=tmp_path)
    assert out == {"active": False, "fired": []}


def test_run_due_inline_jobs_fire_on_first_run(tmp_path: Path, monkeypatch):
    set_state(vault_root=tmp_path, state="on")
    # patch the inline runners so we don't actually generate digests in the test
    digest_calls = []
    miss_calls = []
    monkeypatch.setattr(
        "mnemo.autopilot.core.scheduler._digest_inline",
        lambda vr: digest_calls.append(vr),
    )
    monkeypatch.setattr(
        "mnemo.autopilot.core.scheduler._collect_misses_inline",
        lambda vr: miss_calls.append(vr),
    )
    catchup_calls = []
    monkeypatch.setattr(
        "mnemo.autopilot.core.scheduler._eos_catchup_inline",
        lambda vr: catchup_calls.append(vr),
    )
    # patch detached spawner so we don't actually fork subprocesses
    spawn_calls = []
    monkeypatch.setattr(
        "mnemo.autopilot.core.scheduler.run_detached",
        lambda **kw: spawn_calls.append(kw["name"]),
    )

    out = run_due_jobs(vault_root=tmp_path)
    fired_names = [n for (n, _mode, _ok) in out["fired"]]
    assert "tier0.digest" in fired_names
    assert "tier0.collect-misses" in fired_names
    # detached jobs should also have been requested
    assert "tier1.doctor" in spawn_calls
    assert "tier2.bm25" in spawn_calls
    assert digest_calls == [tmp_path]
    assert miss_calls == [tmp_path]


def test_run_due_skips_recently_run(tmp_path: Path, monkeypatch):
    set_state(vault_root=tmp_path, state="on")
    # mark all jobs as just run
    for name in ("tier0.digest", "tier0.collect-misses", "tier1.doctor",
                 "tier1.sweep", "tier1.telemetry", "tier2.bm25", "tier2.reflex",
                 "tier3.eos-catchup"):
        mark_run(vault_root=tmp_path, name=name)

    spawn_calls = []
    monkeypatch.setattr("mnemo.autopilot.core.scheduler.run_detached",
                        lambda **kw: spawn_calls.append(kw["name"]))
    digest_calls = []
    monkeypatch.setattr(
        "mnemo.autopilot.core.scheduler._digest_inline",
        lambda vr: digest_calls.append(vr),
    )
    miss_calls = []
    monkeypatch.setattr(
        "mnemo.autopilot.core.scheduler._collect_misses_inline",
        lambda vr: miss_calls.append(vr),
    )
    catchup_calls = []
    monkeypatch.setattr(
        "mnemo.autopilot.core.scheduler._eos_catchup_inline",
        lambda vr: catchup_calls.append(vr),
    )

    out = run_due_jobs(vault_root=tmp_path)
    assert out["fired"] == []
    assert spawn_calls == []
    assert digest_calls == []
    assert miss_calls == []


def test_status_summary_lists_all_operations(tmp_path: Path):
    out = status_summary(vault_root=tmp_path)
    names = [r["name"] for r in out]
    assert "tier0.digest" in names
    assert "tier1.doctor" in names
    assert "tier2.bm25" in names
    # all due since none have run AND kill switch is off → "due" must reflect that
    # (should_run gates by kill_switch — off means due=False)
    assert all(r["due"] is False for r in out)


def test_status_summary_due_when_active_and_never_ran(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    out = status_summary(vault_root=tmp_path)
    assert all(r["due"] is True for r in out)


def test_tier3_eos_catchup_fires_when_due(tmp_path, monkeypatch):
    set_state(vault_root=tmp_path, state="on")

    catchup_calls = []
    monkeypatch.setattr(
        "mnemo.autopilot.core.scheduler._eos_catchup_inline",
        lambda vr: catchup_calls.append(vr),
    )
    # Stub the others to keep test focused
    monkeypatch.setattr("mnemo.autopilot.core.scheduler._digest_inline", lambda vr: None)
    monkeypatch.setattr("mnemo.autopilot.core.scheduler._collect_misses_inline", lambda vr: None)
    monkeypatch.setattr("mnemo.autopilot.core.scheduler.run_detached", lambda **kw: None)

    out = run_due_jobs(vault_root=tmp_path)
    fired_names = [n for (n, _mode, _ok) in out["fired"]]
    assert "tier3.eos-catchup" in fired_names
    assert catchup_calls == [tmp_path]


def test_tier3_eos_catchup_in_status_summary(tmp_path):
    out = status_summary(vault_root=tmp_path)
    names = [r["name"] for r in out]
    assert "tier3.eos-catchup" in names
    rec = next(r for r in out if r["name"] == "tier3.eos-catchup")
    assert rec["interval_days"] == 1
