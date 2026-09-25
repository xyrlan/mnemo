"""#503: a dispatched child's worktree goes once its PR merges, and only then.

Real git throughout: an origin, a clone, and a dispatch tree made the way
``dispatch.ensure_worktree`` makes it, whose branch is pushed with ``-u`` as a
child pushes it. Only ``gh`` (the PR), ``lsof`` (the process table) and the
jobs roster are stood in for.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pytest

from mnemo.core.sessions import tree_sweep as ts

NOW = 2_000_000_000.0
LONG_AGO = "2020-01-01T00:00:00.000Z"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


@dataclass
class Job:
    short_id: str = "c0ffee00"
    state: Optional[str] = "stopped"
    tempo: Optional[str] = "idle"
    cwd: Optional[str] = None
    live: Optional[bool] = False
    updated_at: Optional[str] = LONG_AGO

    @property
    def is_blocked(self) -> bool:
        return self.tempo == "blocked"


@pytest.fixture
def world(tmp_path: Path):
    origin = tmp_path / "origin.git"
    _git("init", "--bare", "-b", "master", str(origin), cwd=tmp_path)
    repo = tmp_path / "proj"
    _git("clone", str(origin), str(repo), cwd=tmp_path)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=repo)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git("add", "a.txt", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    _git("push", "-u", "origin", "master", cwd=repo)
    tree = tmp_path / "proj-wt-7"
    _git("worktree", "add", "-b", "fix/issue-7", str(tree), cwd=repo)
    (tree / "fix.txt").write_text("fix\n", encoding="utf-8")
    _git("add", "fix.txt", cwd=tree)
    _git("commit", "-m", "fix", cwd=tree)
    _git("push", "-u", "origin", "fix/issue-7", cwd=tree)
    return {"repo": repo, "tree": tree, "vault": tmp_path / "vault",
            "head": _git("rev-parse", "HEAD", cwd=tree)}


def fake_run(prs: List[dict], cwds: Optional[List[str]] = None):
    calls = []

    def run(args, *, cwd):
        calls.append(list(args))
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, 0, json.dumps(prs), "")
        if args[0] == "lsof":
            out = "".join(f"p1\nn{c}\n" for c in (cwds or []))
            return subprocess.CompletedProcess(args, 0, out, "")
        return ts._run(args, cwd=cwd)

    run.calls = calls
    return run


def merged(head: str, number: int = 11) -> List[dict]:
    return [{"number": number, "state": "MERGED", "headRefOid": head}]


def sweep(world, *, prs=None, jobs=None, cwds=None, now=NOW):
    told = []
    run = fake_run(merged(world["head"]) if prs is None else prs, cwds)
    report = ts.sweep(
        {}, vault_root=world["vault"], repo_root=world["repo"], now=now, run=run,
        reader=lambda: list(jobs if jobs is not None else [Job(cwd=str(world["tree"]))]),
        tell=told.append,
    )
    return report, told


def branches(repo: Path) -> List[str]:
    return _git("branch", "--format=%(refname:short)", cwd=repo).split()


# --- removal ---------------------------------------------------------------


def test_merged_clean_stopped_tree_and_branch_are_removed(world):
    report, told = sweep(world)

    [v] = report.verdicts
    assert v.outcome == ts.REMOVED and v.pr == "#11"
    assert not world["tree"].exists()
    assert "fix/issue-7" not in branches(world["repo"])
    assert "proj-wt-7" not in _git("worktree", "list", cwd=world["repo"])
    assert [t.outcome for t in told] == [ts.REMOVED]
    assert "removed proj-wt-7 and its branch fix/issue-7 (PR #11 merged)" in ts.render(v)


def test_a_tree_whose_job_was_removed_counts_as_gone(world):
    report, _ = sweep(world, jobs=[])
    assert report.verdicts[0].outcome == ts.REMOVED


def test_ignored_files_do_not_keep_a_tree(world):
    (world["tree"] / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _git("add", ".gitignore", cwd=world["tree"])
    _git("commit", "-m", "ignore", cwd=world["tree"])
    _git("push", cwd=world["tree"])
    (world["tree"] / "node_modules").mkdir()
    (world["tree"] / "node_modules" / "x.js").write_text("x", encoding="utf-8")
    world["head"] = _git("rev-parse", "HEAD", cwd=world["tree"])

    report, _ = sweep(world)
    assert report.verdicts[0].outcome == ts.REMOVED
    assert not world["tree"].exists()


def test_branch_is_deleted_with_dash_d_and_kept_when_it_refuses(world):
    # A squash merge leaves the tip outside master; with the tracking ref
    # pruned, `-d` has only HEAD to compare against, and refuses.
    _git("update-ref", "-d", "refs/remotes/origin/fix/issue-7", cwd=world["repo"])
    report, told = sweep(world)

    [v] = report.verdicts
    assert v.outcome == ts.BRANCH_KEPT
    assert not world["tree"].exists()
    assert "fix/issue-7" in branches(world["repo"])
    assert "kept branch fix/issue-7" in ts.render(told[0])


def test_never_force(world, monkeypatch):
    seen = []
    real = ts._run

    def spy(args, *, cwd):
        seen.append(list(args))
        return real(args, cwd=cwd)

    monkeypatch.setattr(ts, "_run", spy)
    sweep(world)
    flat = [a for call in seen for a in call]
    assert "--force" not in flat and "-D" not in flat


# --- refusals: never lose work ---------------------------------------------


def test_dirty_tree_is_kept_and_said_once(world):
    (world["tree"] / "notes.txt").write_text("untracked\n", encoding="utf-8")

    report, told = sweep(world)
    assert report.verdicts[0].outcome == ts.HOLDS_DIRTY
    assert world["tree"].exists()
    assert [t.outcome for t in told] == [ts.HOLDS_DIRTY]
    assert "kept proj-wt-7 (PR #11 merged): it has uncommitted" in ts.render(told[0])

    _, again = sweep(world)
    assert again == []


def test_modified_tracked_file_keeps_the_tree(world):
    (world["tree"] / "fix.txt").write_text("changed\n", encoding="utf-8")
    report, _ = sweep(world)
    assert report.verdicts[0].outcome == ts.HOLDS_DIRTY
    assert world["tree"].exists()


def test_unpushed_commit_keeps_the_tree(world):
    (world["tree"] / "more.txt").write_text("more\n", encoding="utf-8")
    _git("add", "more.txt", cwd=world["tree"])
    _git("commit", "-m", "more", cwd=world["tree"])

    report, told = sweep(world)
    assert report.verdicts[0].outcome == ts.HOLDS_UNPUSHED
    assert world["tree"].exists()
    assert "fix/issue-7" in branches(world["repo"])
    assert [t.outcome for t in told] == [ts.HOLDS_UNPUSHED]


def test_pushed_commits_the_merged_pr_lacks_keep_the_tree(world):
    merged_head = world["head"]
    (world["tree"] / "late.txt").write_text("late\n", encoding="utf-8")
    _git("add", "late.txt", cwd=world["tree"])
    _git("commit", "-m", "after the merge", cwd=world["tree"])
    _git("push", cwd=world["tree"])

    report, _ = sweep(world, prs=merged(merged_head))
    assert report.verdicts[0].outcome == ts.HOLDS_NOT_IN_PR
    assert world["tree"].exists()


def test_an_unknown_pr_head_is_unverifiable_not_removed(world):
    report, _ = sweep(world, prs=merged("f" * 40))
    assert report.verdicts[0].outcome == ts.UNVERIFIABLE
    assert world["tree"].exists()


def test_kept_tree_is_removed_once_it_is_clean_again(world):
    (world["tree"] / "notes.txt").write_text("untracked\n", encoding="utf-8")
    sweep(world)
    (world["tree"] / "notes.txt").unlink()

    report, told = sweep(world)
    assert report.verdicts[0].outcome == ts.REMOVED
    assert [t.outcome for t in told] == [ts.REMOVED]
    assert str(world["tree"]) not in (ts.load_ledger(world["vault"]).get("said") or {})


# --- refusals: not before the child is gone --------------------------------


@pytest.mark.parametrize("job", [
    Job(state="working", tempo="active", live=True),
    Job(state="done", tempo="idle", live=True),
    Job(state="done", tempo="idle", live=False),
    Job(state="blocked", tempo="blocked", live=True),
    Job(state="stopped", tempo="blocked", live=False),
    Job(state="stopped", live=True),
    Job(state="stopped", updated_at=None),
])
def test_child_still_there_keeps_its_tree_silently(world, job):
    job.cwd = str(world["tree"])
    report, told = sweep(world, jobs=[job])
    assert report.verdicts[0].outcome == ts.CHILD_RUNNING
    assert world["tree"].exists()
    assert told == []


def test_just_stopped_child_waits_out_the_grace(world):
    from datetime import datetime, timezone

    fresh = datetime.fromtimestamp(NOW - 60, tz=timezone.utc).isoformat()
    job = Job(cwd=str(world["tree"]), updated_at=fresh)
    report, _ = sweep(world, jobs=[job])
    assert report.verdicts[0].outcome == ts.CHILD_RUNNING

    report, _ = sweep(world, jobs=[job], now=NOW + ts.GRACE_SECONDS)
    assert report.verdicts[0].outcome == ts.REMOVED


def test_every_job_in_the_tree_must_be_gone(world):
    jobs = [Job(cwd=str(world["tree"])),
            Job(short_id="beef0000", state="working", live=True, cwd=str(world["tree"]))]
    report, _ = sweep(world, jobs=jobs)
    assert report.verdicts[0].outcome == ts.CHILD_RUNNING


def test_a_process_inside_the_tree_keeps_it(world):
    report, told = sweep(world, cwds=[str(world["tree"] / "sub")])
    assert report.verdicts[0].outcome == ts.IN_USE
    assert world["tree"].exists() and told == []


def test_the_sweeps_own_cwd_is_skipped(world):
    run = fake_run(merged(world["head"]))
    report = ts.sweep({}, vault_root=world["vault"], repo_root=world["repo"],
                      now=NOW, run=run, reader=lambda: [], tell=lambda v: None,
                      skip=str(world["tree"]))
    assert report.verdicts[0].outcome == ts.IN_USE
    assert not any(c[0] == "gh" for c in run.calls)


# --- which trees and which PRs ---------------------------------------------


def test_open_pr_keeps_the_tree(world):
    prs = merged(world["head"]) + [{"number": 12, "state": "OPEN", "headRefOid": world["head"]}]
    report, told = sweep(world, prs=prs)
    assert report.verdicts[0].outcome == ts.OPEN_PR and told == []


def test_no_pr_keeps_the_tree_silently(world):
    # A losing twin: no PR, no push. Its commits live only in its tree.
    report, told = sweep(world, prs=[])
    assert report.verdicts[0].outcome == ts.NOT_MERGED
    assert world["tree"].exists() and told == []


def test_closed_unmerged_pr_keeps_the_tree(world):
    report, _ = sweep(world, prs=[{"number": 11, "state": "CLOSED", "headRefOid": world["head"]}])
    assert report.verdicts[0].outcome == ts.NOT_MERGED


def test_merged_twin_tree_is_removed(world, tmp_path):
    twin = tmp_path / "proj-wt-8-abc123"
    _git("worktree", "add", "-b", "fix/issue-8-abc123", str(twin), cwd=world["repo"])
    head = _git("rev-parse", "HEAD", cwd=twin)
    _git("push", "-u", "origin", "fix/issue-8-abc123", cwd=twin)

    def prs_for(args, *, cwd):
        if args[0] == "gh":
            branch = args[args.index("--head") + 1]
            h = head if branch == "fix/issue-8-abc123" else world["head"]
            return subprocess.CompletedProcess(args, 0, json.dumps(merged(h)), "")
        if args[0] == "lsof":
            return subprocess.CompletedProcess(args, 0, "", "")
        return ts._run(args, cwd=cwd)

    report = ts.sweep({}, vault_root=world["vault"], repo_root=world["repo"], now=NOW,
                      run=prs_for, reader=lambda: [], tell=lambda v: None)
    assert {v.tree.name: v.outcome for v in report.verdicts} == {
        "proj-wt-7": ts.REMOVED, "proj-wt-8-abc123": ts.REMOVED}


def test_hand_made_worktree_is_never_looked_at(world, tmp_path):
    other = tmp_path / "proj-feature"
    _git("worktree", "add", "-b", "feature", str(other), cwd=world["repo"])
    report, _ = sweep(world)
    assert [v.tree.name for v in report.verdicts] == ["proj-wt-7"]
    assert other.exists()


def test_gh_failure_removes_nothing(world):
    def run(args, *, cwd):
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, 1, "", "not logged in")
        return ts._run(args, cwd=cwd)

    report = ts.sweep({}, vault_root=world["vault"], repo_root=world["repo"], now=NOW,
                      run=run, reader=lambda: [], cwds=lambda: [], tell=lambda v: None)
    assert report.verdicts[0].outcome == ts.NOT_MERGED
    assert world["tree"].exists()


def test_a_second_sweep_holding_off_on_the_lock(world):
    from mnemo.core import locks

    with locks.try_lock(world["vault"] / ".mnemo" / ts.LOCK_NAME) as held:
        assert held
        report, _ = sweep(world)
    assert report.locked and world["tree"].exists()


# --- the hook trigger ------------------------------------------------------


def test_session_start_spawns_at_most_every_interval(world):
    spawned = []
    kw = dict(vault_root=world["vault"], cwd=str(world["tree"]), spawn=spawned.append)

    assert ts.on_session_start({}, now=NOW, **kw) == "spawned"
    assert ts.on_session_start({}, now=NOW + 60, **kw) == "recent"
    assert ts.on_session_start({}, now=NOW + ts.MIN_INTERVAL_SECONDS, **kw) == "spawned"
    assert spawned == [str(world["tree"])] * 2
    # A child's tree and its repo share one clock.
    assert list(ts.load_ledger(world["vault"])["last_run"]) == [
        str(Path(world["repo"]).resolve())]


def test_session_start_off_and_outside_a_repo(world, tmp_path):
    spawned = []
    cfg = {"dispatch": {"removeMergedTrees": False}}
    assert ts.on_session_start(cfg, vault_root=world["vault"], cwd=str(world["repo"]),
                               spawn=spawned.append) == "off"
    plain = tmp_path / "plain"
    plain.mkdir()
    assert ts.on_session_start({}, vault_root=world["vault"], cwd=str(plain),
                               spawn=spawned.append) == "no-repo"
    assert spawned == []


def test_default_is_on():
    from mnemo.core.config import DEFAULTS

    assert DEFAULTS["dispatch"]["removeMergedTrees"] is True
    assert ts.enabled(DEFAULTS)


def test_session_start_spawns_through_the_hooks_chokepoint(world, monkeypatch):
    """#408: every detached `mnemo` a hook starts goes through one stubbed function."""
    from mnemo.hooks import session_start

    seen = []
    monkeypatch.setattr(session_start, "_spawn_detached",
                        lambda args, cwd=None: seen.append((args, cwd)))
    assert ts.on_session_start({}, vault_root=world["vault"], cwd=str(world["repo"])) == "spawned"
    assert seen == [(["tree-sweep"], str(world["repo"]))]


def test_cli_sweeps_the_repo_it_is_typed_in(world, monkeypatch, capsys):
    from mnemo.cli.commands.tree_sweep import cmd_tree_sweep

    seen = {}

    def fake_sweep(cfg, *, vault_root, repo_root, skip):
        seen.update(repo_root=repo_root, skip=skip)
        return ts.SweepReport(verdicts=[
            ts.Verdict(world["tree"], "fix/issue-7", ts.REMOVED, "#11"),
            ts.Verdict(world["tree"], "fix/issue-7", ts.CHILD_RUNNING, "#11"),
        ])

    monkeypatch.setattr(ts, "sweep", fake_sweep)
    monkeypatch.chdir(world["tree"])
    assert cmd_tree_sweep(None) == 0
    assert Path(seen["repo_root"]).resolve() == world["repo"].resolve()
    assert Path(seen["skip"]).resolve() == world["tree"].resolve()
    out = capsys.readouterr().out
    assert "removed proj-wt-7 and its branch fix/issue-7" in out
    assert "proj-wt-7: child-running" in out
