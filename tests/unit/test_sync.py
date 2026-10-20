"""`mnemo sync` in pieces (#642): the tracked set, the machine id, the config
seed, and the command's own edges (no git, off, busy)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mnemo.core import locks, sync
from mnemo.core.sync import seed as seed_mod
from mnemo.core.sync import tracked

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.mark.parametrize("name", [
    "bg-0p3r5wj-T-pytest-of-you-pytest-12",
    "bg-anything",
    "claude-jobs-93f2-tmp-probe",
    "claude-tmp-model-probe-DKBc",
    "501--Users-you-github-app-1a2f",
    "0--x",
    "private-pytest-of-you-test-run0",
    "my-scratchpad-repo",
    "scratchpad",
])
def test_agent_directories_left_by_test_runs_are_litter(name: str):
    assert tracked.is_litter_agent(name)


@pytest.mark.parametrize("name", [
    "app",
    "mnemo",
    "mnemo-wt-642",
    "background-jobs",          # `bg-` is a prefix, not a substring
    "my-bg-app",
    "claude-jobs",              # the prefix needs its dash
    "you-claude-jobs-5b99-tmp-probe",   # a prefix, so not this one
    "501-app",                  # one dash is a name, two is a uid
    "v2--beta",                 # the uid is all digits
    "pytest-plugins",
])
def test_real_project_names_are_not_litter(name: str):
    assert not tracked.is_litter_agent(name)


@pytest.mark.parametrize("rel", [
    "HOME.md",
    ".mnemo/extraction-state.json",
    ".mnemo/sync/primary.json",
    ".mnemo/sync/format.json",
    ".mnemo/sync/config-seed.json",
    "shared/feedback/a.md",
    "shared/_inbox/feedback/a.md",
    "shared/project/app/notes/deep.md",
    "bots/app/briefings/sessions/s1.md",
    "bots/app/memory/MEMORY.md",
])
def test_what_travels(rel: str):
    assert tracked.is_tracked(rel)


@pytest.mark.parametrize("rel", [
    "mnemo.config.json",
    "mnemo.config.json.bak.20260915T233638",
    "HOME.md.bak",
    ".errors.log",
    "README.md",
    "rater-check/pairs.json",
    ".obsidian/app.json",
    "shared/.obsidian/app.json",
    "shared/_archive/feedback/a.md",
    "shared/_archive/sync-losers/20261013T000000Z/shared/feedback/a.md",
    "shared/feedback/a.md.bak",
    "shared/feedback/.DS_Store",
    "shared/feedback/a.abc123.md.tmp",
    ".mnemo/reflex-index.json",
    ".mnemo/sync-state.json",
    ".mnemo/sync-log.jsonl",
    ".mnemo/sync/other.json",
    ".mnemo/sync/nested/primary.json",
    "bots/app/logs/2026-10-13.md",
    "bots/app/working/x.md",
    "bots/app/briefings",
    "bots/bg-x/briefings/sessions/a.md",
    "bots/501--x/memory/MEMORY.md",
    ".git/config",
    "shared/feedback/.git/config",
    "shared/../mnemo.config.json",
    "/etc/passwd",
    "",
])
def test_what_never_travels(rel: str):
    assert not tracked.is_tracked(rel)


def test_machine_id_is_the_sanitised_hostname_unless_config_names_one(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("socket.gethostname", lambda: "Maintainers-MacBook Pro.local")
    assert sync.machine_id({}) == "maintainers-macbook-pro-local"
    assert sync.machine_id({"sync": {"machineId": ""}}) == "maintainers-macbook-pro-local"
    assert sync.machine_id({"sync": {"machineId": "Desk PC"}}) == "desk-pc"
    monkeypatch.setattr("socket.gethostname", lambda: "")
    assert sync.machine_id({}) == "machine"


def test_the_seed_drops_the_vault_root_machine_paths_and_secret_shapes():
    token = "ghp_" + "a1B2c3D4e5" * 4
    raw = {
        "vaultRoot": "/Users/you/mnemo",
        "sync": {"machineId": "mac"},
        "reflex": {"enabled": False, "judge": {"keyEnv": "TYPESAFE_API_KEY", "injectAt": 0.4}},
        "paths": ["/Users/you/a", "relative/ok", "C:\\Users\\you", "~/x", "\\\\server\\share"],
        "service": {"apiToken": token, "password": "hunter2!", "note": "see " + token, "name": "svc"},
        "empty": {"onlyPath": "/tmp/x"},
    }
    assert seed_mod.build_seed(raw) == {
        "reflex": {"enabled": False, "judge": {"keyEnv": "TYPESAFE_API_KEY", "injectAt": 0.4}},
        "paths": ["relative/ok"],
        "service": {"name": "svc"},
    }
    assert token not in json.dumps(seed_mod.build_seed(raw))


# --- the command -----------------------------------------------------------


@pytest.fixture
def cli_vault(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    return tmp_vault


def _main(*argv: str) -> int:
    from mnemo.cli import main

    return main(list(argv))


def test_status_on_a_vault_that_never_synced_says_off(cli_vault: Path, capsys):
    assert _main("sync", "status") == 0
    assert "off" in capsys.readouterr().out
    assert _main("sync", "status", "--json") == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is False
    assert not (cli_vault / ".git").exists()
    assert not (cli_vault / ".mnemo").exists()


def test_status_on_an_unreadable_state_file_says_so_instead_of_off(cli_vault: Path, capsys):
    """#735: a crash zeroed the file and status said ``sync: off``."""
    (cli_vault / ".git").mkdir()
    (cli_vault / ".mnemo").mkdir(exist_ok=True)
    (cli_vault / ".mnemo" / "sync-state.json").write_bytes(b"\0" * 361)
    assert _main("sync", "status") == 0
    out = capsys.readouterr().out
    assert "sync: off" not in out
    assert "sync: unknown" in out and "361 NUL bytes" in out
    assert _main("sync", "status", "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["enabled"] is False and "361 NUL bytes" in report["stateProblem"]


def test_the_state_is_flushed_to_disk_before_it_replaces_the_old_one(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch):
    """#735: NTFS kept a rename whose bytes a crash lost, and the state came
    back 361 NUL bytes."""
    from mnemo.core.sync import state as st

    flushed = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (flushed.append(fd), real_fsync(fd)))
    st.save(tmp_path, {"enabled": True})
    assert len(flushed) == 1
    assert st.load(tmp_path) == {"enabled": True}


def test_pull_and_push_on_a_vault_that_never_synced_change_nothing(cli_vault: Path, capsys):
    assert _main("sync", "pull") == 1
    assert _main("sync", "push") == 1
    assert "mnemo sync init" in capsys.readouterr().out
    assert not (cli_vault / ".git").exists()


@needs_git
def test_the_command_end_to_end_and_busy_returns_zero(cli_vault: Path, tmp_path: Path, capsys,
                                                      monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("socket.gethostname", lambda: "Test Box")
    hub = tmp_path / "hub.git"
    subprocess.run(["git", "init", "--bare", "-q", str(hub)], check=True)
    (cli_vault / "HOME.md").write_text("# home\n", encoding="utf-8")

    assert _main("sync", "init", str(hub)) == 0
    # The id is pinned in this machine's config, so a hostname that changes
    # with the network cannot make the primary stop being the primary.
    config = json.loads((cli_vault / "mnemo.config.json").read_text(encoding="utf-8"))
    assert config["sync"]["machineId"] == "test-box"
    assert Path(config["vaultRoot"]) == cli_vault
    monkeypatch.setattr("socket.gethostname", lambda: "renamed-by-dhcp")
    capsys.readouterr()

    assert _main("sync", "status", "--json") == 0
    status = json.loads(capsys.readouterr().out)
    assert status["enabled"] is True and status["role"] == "primary"
    assert status["machineId"] == "test-box"
    assert _main("sync", "status") == 0
    out = capsys.readouterr().out
    assert "primary" in out and "test-box" in out

    assert _main("sync", "push") == 0
    assert "nothing to push" in capsys.readouterr().out

    with locks.try_lock(sync.lock_path(cli_vault)) as held:
        assert held
        assert _main("sync", "pull") == 0
        assert "busy" in capsys.readouterr().out
        assert _main("sync", "push") == 0
        assert "busy" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["sync", "init", "somewhere.git"], ["sync", "join", "somewhere.git"],
    ["sync", "pull"], ["sync", "push"], ["sync", "status"],
])
def test_a_missing_git_is_one_clear_line_not_a_traceback(cli_vault: Path, capsys, argv,
                                                         monkeypatch: pytest.MonkeyPatch):
    from mnemo.core.sync import git as git_mod

    if argv[1] not in ("init", "join"):
        # A vault that looks synced, so pull, push and status reach for git.
        (cli_vault / ".git").mkdir()
        (cli_vault / ".mnemo").mkdir()
        (cli_vault / ".mnemo" / "sync-state.json").write_text('{"enabled": true}', encoding="utf-8")
    monkeypatch.setattr(git_mod, "GIT", "git-that-is-not-installed-642")
    before = sorted(p.name for p in cli_vault.parent.iterdir())
    assert _main(*argv) == 1
    assert sorted(p.name for p in cli_vault.parent.iterdir()) == before
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "git is not installed" in text
    assert "Traceback" not in text
    assert len(text.strip().splitlines()) == 1


def test_bare_sync_prints_its_subcommands(cli_vault: Path, capsys):
    assert _main("sync") == 2
    out = capsys.readouterr().out
    for word in ("init", "join", "pull", "push", "status"):
        assert word in out


@needs_git
def test_init_refuses_a_vault_that_is_already_someone_elses_git_repository(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "shared").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    hub = tmp_path / "hub.git"
    subprocess.run(["git", "init", "--bare", "-q", str(hub)], check=True)
    out = sync.init(vault, str(hub), machine="mac", config_path=vault / "mnemo.config.json")
    assert not out.ok
    assert "already a git repository" in out.message
    assert not (vault / ".mnemo").exists()
