"""Background sessions are in the roster whichever Claude account runs them (#640).

Claude Code keeps ``jobs/`` and ``daemon/`` under its config dir, and
``CLAUDE_CONFIG_DIR`` moves that dir: the desktop app gives each account its
own ``~/.claude-<slug>``. Everything here goes through the functions the
commands and watchers call — ``read_sessions()`` with no arguments,
``verify_registered``, ``pr_follow.sweep`` with its own reader,
``find_worktree`` — because a test that handed them a root would pass on the
code that read only ``~/.claude/jobs``.

No test reads a real config dir: ``tmp_jobs_dir`` pins the default account's
jobs to an empty temp dir and unsets ``CLAUDE_CONFIG_DIR``, and every account
dir below is under ``tmp_path``.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import List, Optional

import pytest

from mnemo.cli.commands.doctor_checks import misc as doctor_misc
from mnemo.core import claude_cli, twins
from mnemo.core.sessions import delivery, jobs, pr_follow

FULL = "594436f2-a282-4a1c-b04a-08441296047e"
SHORT = FULL[:8]


def _job(root: Path, short_id: str, **fields) -> Path:
    d = root / short_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(fields), encoding="utf-8")
    return d


def _roster(home: Path, **pids: int) -> None:
    (home / "daemon").mkdir(parents=True, exist_ok=True)
    (home / "daemon" / "roster.json").write_text(
        json.dumps({"workers": {sid: {"pid": pid} for sid, pid in pids.items()}}),
        encoding="utf-8",
    )


@pytest.fixture
def account(tmp_path: Path) -> Path:
    """An account-style config dir: its own ``jobs``, nothing else."""
    root = tmp_path / ".claude-someone-example-com"
    (root / "jobs").mkdir(parents=True)
    return root


@pytest.fixture
def on_account(account: Path, monkeypatch) -> Path:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(account))
    return account


@pytest.fixture
def vault(tmp_home: Path) -> Path:
    """The vault the default config resolves to, under the temp home."""
    root = tmp_home / "mnemo"
    root.mkdir()
    return root


def _ids(sessions) -> List[str]:
    return sorted(s.short_id for s in sessions)


# --- which directories are read ---------------------------------------------


def test_a_job_under_the_current_account_is_in_the_roster(
    on_account: Path, tmp_jobs_dir: Path
) -> None:
    _job(on_account / "jobs", "5b99e7f1", state="working", cwd="/w")
    _job(tmp_jobs_dir, "aaaa1111", state="done")

    assert _ids(jobs.read_sessions()) == ["5b99e7f1", "aaaa1111"]


def test_with_the_variable_unset_only_the_default_account_is_read(
    account: Path, tmp_jobs_dir: Path, vault: Path
) -> None:
    _job(account / "jobs", "5b99e7f1", state="working")
    _job(tmp_jobs_dir, "aaaa1111", state="done")

    assert _ids(jobs.read_sessions()) == ["aaaa1111"]
    assert not (vault / ".mnemo").exists()  # nothing to remember, nothing written


def test_an_account_seen_once_is_read_by_a_process_that_was_not_told_about_it(
    account: Path, vault: Path, monkeypatch
) -> None:
    """The watcher's case: started on the default account, child on another.

    The child's own hooks run under its account and read the roster, which is
    what puts the dir on file.
    """
    _job(account / "jobs", "5b99e7f1", state="working")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(account))
    jobs.read_sessions()
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")

    assert _ids(jobs.read_sessions()) == ["5b99e7f1"]


def test_an_account_is_remembered_once_however_it_is_spelled(
    account: Path, vault: Path, monkeypatch
) -> None:
    from mnemo.core.sessions import accounts

    for spelling in (str(account), str(account) + os.sep, str(account / "jobs" / "..")):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", spelling)
        jobs.read_sessions()

    assert [Path(p) for p in accounts.remembered(vault)] == [account]


def test_the_default_dir_named_explicitly_is_still_one_root(
    tmp_home: Path, vault: Path, monkeypatch
) -> None:
    from mnemo.core.sessions import accounts

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_home / ".claude"))

    assert len(accounts.roots()) == 1
    assert accounts.remembered(vault) == []


def test_without_a_vault_nothing_is_written_and_the_account_is_still_read(
    on_account: Path, tmp_home: Path
) -> None:
    _job(on_account / "jobs", "5b99e7f1", state="working")

    assert _ids(jobs.read_sessions()) == ["5b99e7f1"]
    assert not (tmp_home / "mnemo").exists()


@pytest.mark.parametrize("shape", ["missing", "jobs-is-a-file", "garbage-on-file"])
def test_a_root_that_cannot_be_read_is_skipped(
    shape: str, tmp_path: Path, tmp_jobs_dir: Path, vault: Path, monkeypatch
) -> None:
    gone = tmp_path / ".claude-gone"
    if shape == "jobs-is-a-file":
        gone.mkdir()
        (gone / "jobs").write_text("not a directory", encoding="utf-8")
    if shape == "garbage-on-file":
        (vault / ".mnemo").mkdir()
        (vault / ".mnemo" / "claude-config-dirs.jsonl").write_text(
            '{not json\n[1]\n{"config_dir": 7}\n', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(gone))
    _job(tmp_jobs_dir, "aaaa1111", state="done")

    assert _ids(jobs.read_sessions()) == ["aaaa1111"]


# --- one session per id, alive by its own account's daemon -------------------


def test_the_same_id_under_two_accounts_is_one_session(
    on_account: Path, tmp_jobs_dir: Path
) -> None:
    _job(tmp_jobs_dir, SHORT, state="stopped", updatedAt="2026-10-13T10:00:00.000Z")
    _job(on_account / "jobs", SHORT, state="working", updatedAt="2026-10-13T11:00:00.000Z")

    (session,) = jobs.read_sessions()
    assert session.state == "working"


def test_of_two_copies_the_newer_wins_whichever_account_holds_it(
    on_account: Path, tmp_jobs_dir: Path
) -> None:
    _job(tmp_jobs_dir, SHORT, state="working", updatedAt="2026-10-13T11:00:00.000Z")
    _job(on_account / "jobs", SHORT, state="stopped", updatedAt="2026-10-13T10:00:00.000Z")

    (session,) = jobs.read_sessions()
    assert session.state == "working"


def test_of_two_copies_the_one_with_a_running_process_wins(
    on_account: Path, tmp_jobs_dir: Path, tmp_home: Path
) -> None:
    """A pane moved between accounts leaves its old record behind, newer or not."""
    _job(tmp_jobs_dir, SHORT, state="working", tempo="blocked",
         updatedAt="2026-10-13T11:00:00.000Z")
    _roster(tmp_home / ".claude")
    _job(on_account / "jobs", SHORT, state="working", updatedAt="2026-10-13T10:00:00.000Z")
    _roster(on_account, **{SHORT: os.getpid()})

    (session,) = jobs.read_sessions()
    assert session.live is True
    assert session.tempo is None


def test_liveness_is_asked_of_the_account_that_holds_the_job(
    on_account: Path, tmp_jobs_dir: Path, tmp_home: Path
) -> None:
    """Each account has its own daemon. Asking the default one about a child
    on another calls every such child dead."""
    _job(on_account / "jobs", "5b99e7f1", state="working", tempo="blocked")
    _roster(on_account, **{"5b99e7f1": os.getpid()})
    _job(tmp_jobs_dir, "aaaa1111", state="working", tempo="blocked")
    _roster(tmp_home / ".claude")

    by_id = {s.short_id: s for s in jobs.read_sessions()}
    assert by_id["5b99e7f1"].live is True
    assert by_id["5b99e7f1"].is_waiting
    assert by_id["aaaa1111"].live is False


def test_an_explicit_root_is_read_alone(on_account: Path, tmp_path: Path) -> None:
    _job(on_account / "jobs", "5b99e7f1", state="working")
    _job(tmp_path / "given", "bbbb2222", state="done")

    assert _ids(jobs.read_sessions(tmp_path / "given")) == ["bbbb2222"]


def test_the_cwd_scope_applies_across_accounts(
    on_account: Path, tmp_jobs_dir: Path, tmp_path: Path
) -> None:
    repo, other = tmp_path / "app", tmp_path / "other"
    for d in (repo, other, tmp_path / "app-wt-7"):
        d.mkdir()
    _job(on_account / "jobs", "5b99e7f1", state="working", cwd=str(tmp_path / "app-wt-7"))
    _job(on_account / "jobs", "cccc3333", state="working", cwd=str(other))
    _job(tmp_jobs_dir, "aaaa1111", state="done", cwd=str(repo))

    assert _ids(jobs.read_sessions(cwd=str(repo))) == ["5b99e7f1", "aaaa1111"]


# --- the dispatch warning ----------------------------------------------------


def test_the_dispatch_check_finds_the_file_under_the_spawning_account(
    on_account: Path, tmp_path: Path
) -> None:
    tree = tmp_path / "app-wt-640"
    tree.mkdir()
    _job(on_account / "jobs", "5b99e7f1", state="working", cwd=str(tree))

    assert claude_cli.verify_registered("5b99e7f1", cwd=tree) is None


def test_the_dispatch_check_still_fires_when_the_file_is_nowhere(
    on_account: Path, tmp_jobs_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(claude_cli, "claude_version", lambda: "9.9.9")

    message = claude_cli.verify_registered("5b99e7f1", cwd=tmp_path)

    assert message is not None and "jobs-state-json" in message
    # Names where it looked: the spawning account first, and the default too.
    assert str(on_account / "jobs" / "5b99e7f1" / "state.json") in message
    assert str(tmp_jobs_dir) in message


# --- the callers the issue names --------------------------------------------


class _Gh:
    """A red PR on a clean tree: the one event that needs the child."""

    def __call__(self, argv, cwd):
        table = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): "fix/issue-7\n",
            ("git", "status", "--porcelain"): "",
            ("git", "rev-parse", "HEAD"): "abc123\n",
            ("git", "rev-list"): "0\n",
            ("gh", "pr", "list"): json.dumps([{
                "number": 12, "url": "https://github.com/o/r/pull/12", "state": "OPEN",
                "isDraft": False, "additions": 1, "deletions": 1, "changedFiles": 1,
                "headRefOid": "abc123"}]),
            ("gh", "pr", "checks"): json.dumps([{"name": "job0", "bucket": "fail"}]),
            ("gh", "pr", "view"): json.dumps(
                {"mergeable": "MERGEABLE", "comments": [], "reviews": []}),
        }
        for prefix, out in table.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return 0, out, ""
        return 1, "", "unexpected call"


def test_pr_follow_wakes_a_child_whose_job_is_under_another_account(
    account: Path, tmp_path: Path, monkeypatch
) -> None:
    """Before #640 this follow closed as ``session-gone`` and nobody was woken."""
    t0 = 1_790_000_000.0
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    tree = tmp_path / "app-wt-7"
    tree.mkdir()
    _job(account / "jobs", SHORT, state="done", tempo="idle", cwd=str(tree), sessionId=FULL)
    assert pr_follow.register(vault, short_id=SHORT, session_id=FULL, cwd=str(tree),
                              parent=None, now=t0)
    woken: List[str] = []

    def waker(session_id: str, **kw) -> Optional[str]:
        woken.append(session_id)
        return None

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(account))
    report = pr_follow.sweep(
        {}, vault_root=vault, now=t0 + 400, run=_Gh(), wake_fn=waker,
        notify=lambda *a: True, announce=False,
    )

    assert report.closed == []
    assert woken == [FULL]


def _git(args: List[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def test_find_worktree_names_a_child_whose_job_is_under_another_account(
    on_account: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    tree = tmp_path / "app-wt-7"
    tree.mkdir()
    _job(on_account / "jobs", "5b99e7f1", state="done", cwd=str(tree))

    assert delivery.find_worktree("5b99e7f1", repo_root=repo) == tree
    assert delivery.find_worktree("5b99", repo_root=repo) == tree


def test_doctor_counts_sessions_on_every_account(
    on_account: Path, tmp_jobs_dir: Path, capsys
) -> None:
    _job(on_account / "jobs", "5b99e7f1", state="working", tempo="blocked")
    _job(tmp_jobs_dir, "aaaa1111", state="done")
    _job(tmp_jobs_dir, "5b99e7f1", state="stopped")  # the same id: counted once

    assert doctor_misc._doctor_check_background_sessions(None) is True
    assert "2 background sessions" in capsys.readouterr().out


def test_a_twin_is_read_from_the_account_that_ran_it(on_account: Path) -> None:
    _job(on_account / "jobs", "5b99e7f1", state="done", tokens=9)

    assert twins._state("5b99e7f1") == {"state": "done", "tokens": 9}
