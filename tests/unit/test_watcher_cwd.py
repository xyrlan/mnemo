"""Long-lived watchers never run inside a dispatched child's tree (#506).

``pr-follow``, ``child-notices`` and ``resume --watch`` live for up to a day.
Started from a hook, they inherited its cwd — usually a child's worktree — and
the merged-tree sweep (#503) keeps any tree a process has its cwd in, so a
merged tree stayed on disk for as long as a watcher lived. Each spawn now
gets :func:`session_start.watcher_cwd`: outside the tree, and chosen so that
``load_config`` from there resolves the same file it did from the hook.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from mnemo.core import config
from mnemo.core.sessions import child_notices, pr_follow, rewake
from mnemo.hooks import session_start


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A global config naming a vault, and a dispatch tree the hook runs in."""
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg_file = tmp_path / "home" / "mnemo" / "mnemo.config.json"
    cfg_file.parent.mkdir(parents=True)
    cfg_file.write_text(json.dumps({"vaultRoot": str(vault)}), encoding="utf-8")
    tree = tmp_path / "app-wt-7"
    tree.mkdir()
    monkeypatch.delenv("MNEMO_CONFIG_PATH", raising=False)
    monkeypatch.setattr(config, "default_config_path",
                        lambda: config._find_local_config() or cfg_file)
    monkeypatch.chdir(tree)
    return {"vault": vault, "tree": tree, "cfg": cfg_file}


def _outside(path: str, tree: Path) -> bool:
    here, tree = Path(path).resolve(), tree.resolve()
    return here != tree and tree not in here.parents


# --- where ------------------------------------------------------------------


def test_a_global_install_runs_its_watchers_in_the_vault(world):
    assert Path(session_start.watcher_cwd()).resolve() == world["vault"].resolve()


def test_the_config_env_var_leaves_the_cwd_to_the_vault(world, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    env_cfg = tmp_path / "env.json"
    env_cfg.write_text(json.dumps({"vaultRoot": str(other)}), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(env_cfg))
    monkeypatch.setattr(config, "default_config_path", lambda: env_cfg)
    (world["tree"] / ".mnemo").mkdir()
    (world["tree"] / ".mnemo" / "mnemo.config.json").write_text("{}", encoding="utf-8")
    assert Path(session_start.watcher_cwd()).resolve() == other.resolve()


def test_a_project_install_runs_its_watchers_where_its_config_resolves(world, tmp_path, monkeypatch):
    """``init --project`` keeps its config in ``<root>/.mnemo``, found only
    from ``<root>`` — so the watcher runs there, and loads the same file."""
    root = tmp_path / "proj"
    (root / ".mnemo").mkdir(parents=True)
    local = root / ".mnemo" / "mnemo.config.json"
    local.write_text(json.dumps({"vaultRoot": str(root / ".mnemo")}), encoding="utf-8")
    monkeypatch.chdir(root)
    before = config.default_config_path()

    where = session_start.watcher_cwd()
    assert Path(where).resolve() == root.resolve()
    monkeypatch.chdir(where)
    assert config.default_config_path().resolve() == before.resolve() == local.resolve()


def test_no_vault_yet_falls_back_to_home(world, tmp_path, monkeypatch):
    world["cfg"].write_text(json.dumps({"vaultRoot": str(tmp_path / "nowhere")}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    assert session_start.watcher_cwd() == os.path.expanduser("~")
    assert _outside(session_start.watcher_cwd(), world["tree"])


def test_the_watcher_loads_the_config_the_hook_loaded(world):
    before = config.load_config()
    os.chdir(session_start.watcher_cwd())
    assert config.load_config() == before


# --- the three spawns ---------------------------------------------------------


def _capture_detached(monkeypatch):
    seen = []
    monkeypatch.setattr(session_start, "_spawn_detached",
                        lambda args, cwd=None: seen.append((args, cwd)))
    return seen


def test_pr_follow_is_spawned_outside_the_tree(world, monkeypatch):
    seen = _capture_detached(monkeypatch)
    pr_follow._spawn_watcher()
    (args, cwd), = seen
    assert args == ["pr-follow"]
    assert _outside(cwd, world["tree"])
    assert Path(cwd).resolve() == world["vault"].resolve()


def test_child_notices_is_spawned_outside_the_tree(world, monkeypatch):
    seen = _capture_detached(monkeypatch)
    child_notices._spawn_watcher()
    (args, cwd), = seen
    assert args == ["child-notices"]
    assert _outside(cwd, world["tree"])
    assert Path(cwd).resolve() == world["vault"].resolve()


@pytest.mark.real_spawn
def test_resume_watch_is_spawned_outside_the_tree(world, monkeypatch):
    seen = []

    class _Popen:
        def __init__(self, argv, **kw):
            seen.append((list(argv), kw.get("cwd")))

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    rewake._spawn_watcher()
    (argv, cwd), = seen
    assert argv[-2:] == ["resume", "--watch"]
    assert _outside(cwd, world["tree"])
    assert Path(cwd).resolve() == world["vault"].resolve()


@pytest.mark.real_spawn
def test_the_real_chokepoint_hands_the_cwd_to_popen(world, monkeypatch):
    """End to end through ``_detach.spawn``: the process starts in the vault."""
    seen = []

    class _Popen:
        pid = 1

        def __init__(self, argv, **kw):
            seen.append((list(argv), kw.get("cwd")))

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    pr_follow._spawn_watcher()
    child_notices._spawn_watcher()
    assert [argv[-1] for argv, _ in seen] == ["pr-follow", "child-notices"]
    for _argv, cwd in seen:
        assert Path(cwd).resolve() == world["vault"].resolve()
