"""The statusline's sync segment (#644) in its three states: in step, unpushed, behind.

The segment reads the summary the doctor rows read, with the count of
pending files cached for two minutes, so the first half tests the segment
over summaries and the second renders the whole statusline on a vault really
initialised against a bare hub under ``tmp_path``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mnemo import statusline as sl
from mnemo.core import sync
from mnemo.core.sync import health

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _summary(**over):
    base = {"enabled": True, "pending": 0, "ahead": 0, "behind": 0,
            "pullStale": False, "lastPullAgoSeconds": 60}
    base.update(over)
    return base


def test_in_step_shows_nothing():
    assert health.segment(_summary()) == ""
    assert health.segment(None) == ""
    assert health.segment({"enabled": False}) == ""


def test_unpushed_work_shows_the_count():
    assert health.segment(_summary(pending=3)) == "sync 3↑"
    assert health.segment(_summary(ahead=1)) == "sync ↑"


def test_behind_shows_how_long():
    assert health.segment(_summary(pullStale=True, lastPullAgoSeconds=2 * 86400)) == "sync 2 d behind"
    assert health.segment(_summary(pullStale=True, lastPullAgoSeconds=None)) == "sync never pulled"
    both = health.segment(_summary(pending=2, pullStale=True, lastPullAgoSeconds=5 * 3600))
    assert both == "sync 2↑ 5 h behind"


# --- through render, on a real vault ------------------------------------------


@pytest.fixture(autouse=True)
def _no_project_resolution(monkeypatch):
    def stub(cwd):
        return type("A", (), {"name": None, "repo_root": cwd, "has_git": False})()

    monkeypatch.setattr("mnemo.core.agent.resolve_agent", stub)
    monkeypatch.setattr("mnemo.core.agent.resolve_canonical_agent", stub)


@pytest.fixture
def git_env(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def claude_json(tmp_path: Path) -> Path:
    path = tmp_path / ".claude.json"
    path.write_text(json.dumps({"mcpServers": {"mnemo": {"command": "python"}}}), encoding="utf-8")
    return path


def _primary(tmp_path: Path, monkeypatch) -> Path:
    hub = tmp_path / "hub.git"
    subprocess.run(["git", "init", "--bare", "-q", str(hub)], check=True)
    vault = tmp_path / "mac" / "mnemo"
    (vault / "shared").mkdir(parents=True)
    (vault / "bots").mkdir()
    (vault / "HOME.md").write_text("# home\n", encoding="utf-8")
    config = vault / "mnemo.config.json"
    config.write_text(json.dumps({"vaultRoot": str(vault), "sync": {"machineId": "mac"}}),
                      encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(config))
    assert sync.init(vault, str(hub), machine="mac", config_path=config).ok
    return vault


def _briefing(vault: Path, name: str) -> Path:
    path = vault / "bots" / "app" / "briefings" / "sessions" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {name}\n", encoding="utf-8")
    return path


def test_sync_off_renders_no_segment(tmp_vault, claude_json):
    assert "sync" not in sl.render(tmp_vault, claude_json, cwd=str(tmp_vault))


@needs_git
def test_the_three_states_through_render(tmp_path, monkeypatch, git_env, claude_json):
    vault = _primary(tmp_path, monkeypatch)
    # In step: just initialised, nothing waiting, pulled this second.
    assert "sync" not in sl.render(vault, claude_json, cwd=str(vault))

    # Unpushed: a briefing written since. The count is cached, so the engine
    # drops the cache after each run and a test drops it after a write.
    _briefing(vault, "s1")
    health.drop_cache(vault)
    assert sl.render(vault, claude_json, cwd=str(vault)).endswith("sync 1↑")

    # Pushed: the engine dropped the cache itself; the segment goes away.
    assert sync.push(vault, machine="mac", config_path=vault / "mnemo.config.json").outcome == "ok"
    assert "sync" not in sl.render(vault, claude_json, cwd=str(vault))

    # Behind: the last successful pull is older than the threshold.
    state_path = vault / ".mnemo" / "sync-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["lastPull"] = "2026-01-01T00:00:00Z"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    rendered = sl.render(vault, claude_json, cwd=str(vault))
    assert rendered.endswith(" d behind") and "sync " in rendered


@needs_git
def test_the_count_is_cached_and_not_walked_on_every_render(tmp_path, monkeypatch, git_env, claude_json):
    vault = _primary(tmp_path, monkeypatch)
    _briefing(vault, "s1")
    health.drop_cache(vault)
    assert health.summary(vault, "mac")["pending"] == 1
    cached = json.loads(health.cache_path(vault).read_text(encoding="utf-8"))
    assert cached["pending"] == 1 and "at" in cached

    def boom(_vault, _machine=None):
        raise AssertionError("walked")

    monkeypatch.setattr(health, "_count", boom)
    again = health.summary(vault, "mac")
    assert again["pending"] == 1 and again["countsAgeSeconds"] >= 0
    assert sl.render(vault, claude_json, cwd=str(vault)).endswith("sync 1↑")
    with pytest.raises(AssertionError):
        health.summary(vault, "mac", max_age=0)
