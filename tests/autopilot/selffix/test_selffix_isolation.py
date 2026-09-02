"""End-to-end tests: self-fix must build its PR without touching the live checkout.

These lock the three defects found on 2026-08-01:

1. ``git checkout -b`` moved the live ``HEAD``, so a human's next commit
   landed on the autopilot's branch.
2. No commit was ever made, so the pushed branch was empty and the PR
   carried no diff.
3. ``repo_root`` came from the process cwd, so the branch was cut in
   whatever repo the autopilot was launched from — not the one holding
   the edited files.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from mnemo.autopilot.selffix.dead_rule_sweep import DeadRule, open_dead_rule_pr
from mnemo.autopilot.selffix.doctor_fixer import DoctorWarning, open_doctor_fix_pr
from mnemo.autopilot.selffix.telemetry_doctor import TelemetryAnomaly, open_telemetry_fix_pr


@pytest.fixture(autouse=True)
def _network_on(monkeypatch):
    """These tests exercise the network path, which is off by default.

    ``autopilot.network.enabled`` gates every ``gh`` call site; turning it on
    here keeps this module testing what it was written to test. The gate's own
    coverage lives in ``tests/unit/test_autopilot_network_gate.py``.
    """
    from mnemo.autopilot.core import network

    monkeypatch.setattr(network, "enabled", lambda cfg=None: True)


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A vault that is also a git repo, with the autopilot kill switch on."""
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    (root / ".mnemo" / "autopilot.json").write_text(
        json.dumps({"schema_version": 1, "state": "on", "paused_until": None,
                    "last_changed_at": None, "last_changed_by": None}),
        encoding="utf-8",
    )
    briefings = root / "briefings"
    briefings.mkdir()
    (briefings / "keep.md").write_text("x", encoding="utf-8")
    _git("init", "-b", "master", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    return root


def _make_rule(vault: Path, name: str, sources: list) -> Path:
    shared_dir = vault / "shared" / "feedback"
    shared_dir.mkdir(parents=True, exist_ok=True)
    body = "x" * 60
    content = "---\ntype: feedback\ntags:\n  - test\nsources:\n" + "".join(
        f"  - {s}\n" for s in sources
    ) + f"---\n{body}\n"
    path = shared_dir / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _commit_all(vault: Path) -> None:
    _git("add", "-A", cwd=vault)
    _git("commit", "-m", "seed", cwd=vault)


def _worktree_count(root: Path) -> int:
    return len([l for l in _git("worktree", "list", cwd=root).splitlines() if l.strip()])


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_fix_commits_the_diff_without_moving_live_head(vault: Path) -> None:
    rule = _make_rule(vault, "my-rule", ["briefings/missing.md", "briefings/keep.md"])
    _commit_all(vault)
    head_before = _git("rev-parse", "HEAD", cwd=vault)
    warnings = [DoctorWarning(
        kind="source_path_missing", rule_path=rule, detail="briefings/missing.md"
    )]

    with patch("mnemo.autopilot.selffix.doctor_fixer._run_pytest", return_value=True), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.push_branch", return_value=True), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.open_pr", return_value=99) as mock_pr:
        result = open_doctor_fix_pr(warnings, vault_root=vault, repo_root=vault)

    assert result == 99
    # Live checkout is exactly where the human left it
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=vault) == "master"
    assert _git("rev-parse", "HEAD", cwd=vault) == head_before
    # The branch that was pushed actually carries the fix
    branch = mock_pr.call_args.kwargs["branch"]
    changed = _git("show", "--name-only", "--format=", branch, cwd=vault)
    assert "shared/feedback/my-rule.md" in changed
    assert "briefings/missing.md" not in _git("show", f"{branch}:shared/feedback/my-rule.md", cwd=vault)
    # Worktree torn down
    assert _worktree_count(vault) == 1


def test_doctor_fix_leaves_a_dirty_live_tree_alone(vault: Path) -> None:
    rule = _make_rule(vault, "my-rule", ["briefings/missing.md", "briefings/keep.md"])
    _commit_all(vault)
    wip = vault / "briefings" / "wip.md"
    wip.write_text("human work in progress\n", encoding="utf-8")
    warnings = [DoctorWarning(
        kind="source_path_missing", rule_path=rule, detail="briefings/missing.md"
    )]

    with patch("mnemo.autopilot.selffix.doctor_fixer._run_pytest", return_value=True), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.push_branch", return_value=True), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.open_pr", return_value=99):
        open_doctor_fix_pr(warnings, vault_root=vault, repo_root=vault)

    assert wip.read_text(encoding="utf-8") == "human work in progress\n"
    assert "?? briefings/wip.md" in _git("status", "--porcelain", cwd=vault)


def test_doctor_fix_applies_cures_but_opens_no_pr_without_a_repo(vault: Path) -> None:
    """A vault outside version control still gets healed — it just gets no PR."""
    rule = _make_rule(vault, "my-rule", ["briefings/missing.md", "briefings/keep.md"])
    warnings = [DoctorWarning(
        kind="source_path_missing", rule_path=rule, detail="briefings/missing.md"
    )]

    with patch("mnemo.autopilot.selffix.doctor_fixer._gh.open_pr") as mock_pr, \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.create_worktree") as mock_wt:
        result = open_doctor_fix_pr(warnings, vault_root=vault, repo_root=None)

    assert result is None
    mock_pr.assert_not_called()
    mock_wt.assert_not_called()
    assert "briefings/missing.md" not in rule.read_text(encoding="utf-8")


def test_doctor_fix_opens_no_pr_when_the_commit_is_empty(vault: Path) -> None:
    """No diff reached the worktree — pushing an empty branch would make a junk PR."""
    rule = _make_rule(vault, "my-rule", ["briefings/missing.md", "briefings/keep.md"])
    _commit_all(vault)
    warnings = [DoctorWarning(
        kind="source_path_missing", rule_path=rule, detail="briefings/missing.md"
    )]

    with patch("mnemo.autopilot.selffix.doctor_fixer._run_pytest", return_value=True), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.commit_all", return_value=False), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.push_branch") as mock_push, \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.open_pr") as mock_pr:
        result = open_doctor_fix_pr(warnings, vault_root=vault, repo_root=vault)

    assert result is None
    mock_push.assert_not_called()
    mock_pr.assert_not_called()
    assert _worktree_count(vault) == 1


def test_doctor_fix_tears_down_the_worktree_when_pytest_fails(vault: Path) -> None:
    rule = _make_rule(vault, "my-rule", ["briefings/missing.md", "briefings/keep.md"])
    _commit_all(vault)
    warnings = [DoctorWarning(
        kind="source_path_missing", rule_path=rule, detail="briefings/missing.md"
    )]

    with patch("mnemo.autopilot.selffix.doctor_fixer._run_pytest", return_value=False), \
         patch("mnemo.autopilot.selffix.doctor_fixer._gh.open_pr") as mock_pr:
        result = open_doctor_fix_pr(warnings, vault_root=vault, repo_root=vault)

    assert result is None
    mock_pr.assert_not_called()
    assert _worktree_count(vault) == 1


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------


def test_sweep_commit_records_both_the_deletion_and_the_archive(vault: Path) -> None:
    rule = _make_rule(vault, "dead-rule", ["briefings/keep.md"])
    _commit_all(vault)
    head_before = _git("rev-parse", "HEAD", cwd=vault)
    dead = [DeadRule(slug="dead-rule", rule_path=rule, last_seen_days=999)]

    with patch("mnemo.autopilot.selffix.dead_rule_sweep._run_pytest", return_value=True), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.push_branch", return_value=True), \
         patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.open_pr", return_value=55) as mock_pr:
        result = open_dead_rule_pr(dead, vault_root=vault, repo_root=vault)

    assert result == 55
    assert _git("rev-parse", "HEAD", cwd=vault) == head_before
    branch = mock_pr.call_args.kwargs["branch"]
    # --no-renames: assert the move as a delete + add pair, so the test fails
    # if the old path is silently left behind rather than removed.
    changed = _git(
        "show", "--name-status", "--no-renames", "--format=", branch, cwd=vault
    ).splitlines()
    assert "D\tshared/feedback/dead-rule.md" in changed
    assert "A\tshared/_archive/dead-rule.md" in changed
    assert _worktree_count(vault) == 1


def test_sweep_archives_but_opens_no_pr_without_a_repo(vault: Path) -> None:
    rule = _make_rule(vault, "dead-rule", ["briefings/keep.md"])
    dead = [DeadRule(slug="dead-rule", rule_path=rule, last_seen_days=999)]

    with patch("mnemo.autopilot.selffix.dead_rule_sweep._gh.open_pr") as mock_pr:
        result = open_dead_rule_pr(dead, vault_root=vault, repo_root=None)

    assert result is None
    mock_pr.assert_not_called()
    assert (vault / "shared" / "_archive" / "dead-rule.md").exists()


# ---------------------------------------------------------------------------
# telemetry — an anomaly report has no diff, so it is an issue, not a PR
# ---------------------------------------------------------------------------


def test_telemetry_opens_an_issue_and_never_branches(vault: Path) -> None:
    _commit_all(vault)
    head_before = _git("rev-parse", "HEAD", cwd=vault)
    anomalies = [TelemetryAnomaly(
        kind="prompt_tokens_null", detail="null in 4/10 entries", affected_count=4
    )]

    with patch("mnemo.autopilot.selffix.telemetry_doctor._gh.open_issue", return_value=7) as mock_issue, \
         patch("mnemo.autopilot.selffix.telemetry_doctor._gh.create_worktree") as mock_wt:
        result = open_telemetry_fix_pr(anomalies, vault_root=vault, repo_root=vault)

    assert result == 7
    mock_wt.assert_not_called()
    assert _git("rev-parse", "HEAD", cwd=vault) == head_before
    body = mock_issue.call_args.kwargs["body"]
    assert "prompt_tokens_null" in body
