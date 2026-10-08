"""The real-path guard blames a test for its own writes only (#612).

The autouse ``_real_vault_guard`` used to stat ``~/.claude/projects`` before
and after every test, so any Claude Code session on the machine creating a
project directory -- every dispatched child does -- failed whichever test was
running. These tests drive the guard over temp roots: writes the test makes,
in-process or through a process it starts, are caught; writes by a process
the test did not start are not.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._real_path_guard import RealPathGuard, encode_project_dir


@pytest.fixture
def roots(tmp_path: Path):
    home = tmp_path / "realhome"
    vault = home / "mnemo"
    projects = home / ".claude" / "projects"
    for d in (vault / "shared", projects, home / ".codex"):
        d.mkdir(parents=True)
    (vault / ".errors.log").write_text("", encoding="utf-8")
    existing = projects / "-Users-you-github-mnemo"
    existing.mkdir()
    guard = RealPathGuard(
        write_roots=[vault, projects, home / ".cursor", home / ".codex"],
        stat_paths=[vault / ".errors.log", vault / "shared", home / ".cursor", home / ".codex"],
        projects_dir=projects,
        home=home,
    )
    return guard, home, vault, projects


def _run(code: str, *args: str) -> None:
    subprocess.run([sys.executable, "-c", code, *args], check=True)


def _unrelated(code: str, *args: str) -> "subprocess.Popen":
    """A process the test did not start: launched before the guard opens."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.2)\n" + code, *args]
    )


# --- writes by processes the test did not start -----------------------------


def test_an_unrelated_session_creating_a_project_dir_does_not_fail_the_test(roots, tmp_path):
    guard, _home, _vault, projects = roots
    other = _unrelated("import os, sys; os.mkdir(sys.argv[1])", str(projects / "-Users-you-github-mnemo-wt-611"))
    guard.begin(own_paths=[tmp_path / "test-own"])
    other.wait()
    assert guard.end() == []


def test_an_unrelated_project_dir_is_not_blamed_on_a_test_that_spawns(roots, tmp_path):
    guard, _home, _vault, projects = roots
    other = _unrelated("import os, sys; os.mkdir(sys.argv[1])", str(projects / "-Users-you-github-mnemo-wt-611"))
    guard.begin(own_paths=[tmp_path / "test-own"])
    _run("pass")  # the test starts a process of its own
    other.wait()
    assert guard.end() == []


def test_an_unrelated_error_log_append_does_not_fail_a_test_that_spawns_nothing(roots, tmp_path):
    guard, _home, vault, _projects = roots
    other = _unrelated(
        "import sys; open(sys.argv[1], 'a').write('{}\\n')", str(vault / ".errors.log")
    )
    guard.begin(own_paths=[tmp_path / "test-own"])
    other.wait()
    assert guard.end() == []


def test_an_unrelated_error_log_append_is_not_blamed_on_a_test_that_ran_git(roots, tmp_path):
    """The flake this guard used to have: a test runs a harmless child under
    its temp HOME while another session's hook logs an error."""
    guard, _home, vault, _projects = roots
    other = _unrelated(
        "import sys; open(sys.argv[1], 'a').write('{}\\n')", str(vault / ".errors.log")
    )
    guard.begin(own_paths=[tmp_path / "test-own"])
    _run("pass")
    other.wait()
    assert guard.end() == []


# --- writes the test makes in its own process -------------------------------


@pytest.mark.parametrize(
    "write",
    [
        lambda p: (p / "note.md").write_text("x", encoding="utf-8"),
        lambda p: (p / "sub").mkdir(),
        lambda p: os.close(os.open(str(p / "fd.txt"), os.O_WRONLY | os.O_CREAT)),
        lambda p: open(str(p / "app.log"), "a", encoding="utf-8").close(),
    ],
    ids=["write_text", "mkdir", "os.open", "append"],
)
def test_an_in_process_write_under_a_watched_root_is_caught(roots, tmp_path, write):
    guard, _home, vault, _projects = roots
    guard.begin(own_paths=[tmp_path / "test-own"])
    write(vault / "shared")
    violations = guard.end()
    # Windows reports the path case-folded (``os.path.normcase``).
    assert violations and os.path.normcase(str(vault / "shared")) in violations[0]


def test_an_in_process_write_deep_inside_an_existing_project_dir_is_caught(roots, tmp_path):
    """The old shallow mtime check never saw a write two levels down."""
    guard, _home, _vault, projects = roots
    memory = projects / "-Users-you-github-mnemo" / "memory"
    memory.mkdir()
    guard.begin(own_paths=[tmp_path / "test-own"])
    (memory / "MEMORY.md").write_text("- x\n", encoding="utf-8")
    assert guard.end()


def test_a_rename_into_a_watched_root_is_caught(roots, tmp_path):
    guard, home, _vault, _projects = roots
    src = tmp_path / "staged.toml"
    src.write_text("x", encoding="utf-8")
    guard.begin(own_paths=[tmp_path / "test-own"])
    os.replace(str(src), str(home / ".codex" / "config.toml"))
    assert guard.end()


def test_reading_a_watched_root_is_not_a_write(roots, tmp_path):
    guard, _home, vault, projects = roots
    guard.begin(own_paths=[tmp_path / "test-own"])
    (vault / ".errors.log").read_text(encoding="utf-8")
    sorted(os.listdir(str(projects)))
    assert guard.end() == []


def test_a_write_outside_the_window_is_not_recorded(roots, tmp_path):
    guard, _home, vault, _projects = roots
    (vault / "shared" / "before.md").write_text("x", encoding="utf-8")
    guard.begin(own_paths=[tmp_path / "test-own"])
    assert guard.end() == []


# --- writes the test makes through a process it starts ----------------------


def test_a_spawned_process_creating_a_project_dir_for_the_tests_path_is_caught(roots, tmp_path):
    guard, _home, _vault, projects = roots
    own = tmp_path / "test-own"
    leaked = projects / (encode_project_dir(str(own / "repo")))
    guard.begin(own_paths=[own])
    _run("import os, sys; os.makedirs(sys.argv[1])", str(leaked))
    violations = guard.end()
    assert violations and leaked.name in violations[0]


def test_a_spawned_process_appending_to_the_error_log_is_caught(roots, tmp_path):
    guard, _home, vault, _projects = roots
    guard.begin(own_paths=[tmp_path / "test-own"])
    _run("import sys; open(sys.argv[1], 'a').write('{}\\n')", str(vault / ".errors.log"))
    violations = guard.end()
    assert violations and ".errors.log" in violations[0]


def test_a_spawned_process_given_the_real_home_is_caught(roots, tmp_path):
    """No watched path in its argv: it finds the vault through HOME, as a
    hook run with the developer's environment would."""
    guard, home, _vault, _projects = roots
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    guard.begin(own_paths=[tmp_path / "test-own"])
    subprocess.run(
        [sys.executable, "-c",
         "import os; open(os.path.join(os.environ['HOME'], 'mnemo', '.errors.log'), 'a').write('{}\\n')"],
        env=env, check=True,
    )
    violations = guard.end()
    assert violations and ".errors.log" in violations[0]


def test_a_spawned_process_given_the_real_config_path_is_caught(roots, tmp_path):
    guard, _home, vault, _projects = roots
    env = dict(os.environ, MNEMO_CONFIG_PATH=str(vault / "mnemo.config.json"))
    guard.begin(own_paths=[tmp_path / "test-own"])
    subprocess.run(
        [sys.executable, "-c",
         "import os; d = os.path.dirname(os.environ['MNEMO_CONFIG_PATH']);"
         " open(os.path.join(d, '.errors.log'), 'a').write('{}\\n')"],
        env=env, check=True,
    )
    assert guard.end()


def test_a_sibling_of_a_watched_root_does_not_make_a_child_reach_it(roots, tmp_path):
    """A child run in ``~/github/mnemo-wt-612`` was not given ``~/mnemo``."""
    guard, home, vault, _projects = roots
    sibling = Path(str(vault) + "-wt-612")
    sibling.mkdir()
    other = _unrelated(
        "import sys; open(sys.argv[1], 'a').write('{}\\n')", str(vault / ".errors.log")
    )
    guard.begin(own_paths=[tmp_path / "test-own"])
    subprocess.run([sys.executable, "-c", "pass"], cwd=str(sibling), check=True)
    other.wait()
    assert guard.end() == []


def test_a_spawned_process_creating_a_watched_dir_is_caught(roots, tmp_path):
    guard, home, _vault, _projects = roots
    guard.begin(own_paths=[tmp_path / "test-own"])
    _run("import os, sys; os.mkdir(sys.argv[1])", str(home / ".cursor"))
    assert guard.end()


# --- attribution ------------------------------------------------------------


def test_project_dir_names_follow_claude_codes_encoding():
    assert encode_project_dir("/Users/you/.claude/jobs/1f87be87/tmp") == "-Users-you--claude-jobs-1f87be87-tmp"


def test_a_sibling_worktree_is_not_attributed_to_this_one(roots, tmp_path):
    """``mnemo-wt-6121`` is not under ``mnemo-wt-612``: the match needs a boundary."""
    guard, _home, _vault, projects = roots
    own = tmp_path / "mnemo-wt-612"
    guard.begin(own_paths=[own])
    _run("import os, sys; os.mkdir(sys.argv[1])", str(projects / (encode_project_dir(str(own)) + "1")))
    assert guard.end() == []


def test_the_suite_guard_watches_the_real_paths():
    from tests import conftest

    guard = conftest._REAL_PATH_GUARD
    assert guard.projects_dir == conftest._REAL_HOME / ".claude" / "projects"
    watched = {str(p) for p in guard.write_roots}
    for p in (conftest._REAL_VAULT, conftest._REAL_HOME / ".cursor", conftest._REAL_HOME / ".codex"):
        assert str(p) in watched
