"""Every ``gh`` call site is gated on ``autopilot.network.enabled``.

mnemo's README promises zero network calls. The autopilot used to open GitHub
issues and PRs with the default config. These tests hold the line: with the
flag off (the default) nothing spawns a ``gh`` process, and with the flag on
the old path still runs — the mutation guard proving each gate is load-bearing.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from mnemo.autopilot.core import labels as labels_mod
from mnemo.autopilot.core import network
from mnemo.autopilot.insights.digest import DigestData, post_digest_issue
from mnemo.autopilot.selffix import _gh, outcome_poller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class ProcessSpy:
    """Records every argv handed to subprocess.run / subprocess.Popen."""

    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.calls: List[list] = []
        self.returncode = returncode
        self.stdout = stdout

    def __call__(self, args, *a, **kw):
        self.calls.append(list(args))
        return subprocess.CompletedProcess(
            args=list(args), returncode=self.returncode, stdout=self.stdout, stderr=""
        )

    @property
    def gh_calls(self) -> List[list]:
        return [c for c in self.calls if c and str(c[0]) == "gh"]


@pytest.fixture
def spy(monkeypatch) -> ProcessSpy:
    """Spy on both process-spawning entry points across every module."""
    s = ProcessSpy()
    monkeypatch.setattr(subprocess, "run", s)
    monkeypatch.setattr(subprocess, "Popen", s)
    return s


@pytest.fixture
def net_off(monkeypatch):
    monkeypatch.setattr(network, "enabled", lambda cfg=None: False)


@pytest.fixture
def net_on(monkeypatch):
    monkeypatch.setattr(network, "enabled", lambda cfg=None: True)


def _digest() -> DigestData:
    return DigestData(date_str="2026-09-02")


def _git_vault(tmp_path: Path) -> Path:
    """A vault that is a real git repo, so the PR path is otherwise reachable."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


# ---------------------------------------------------------------------------
# digest.post_digest_issue
# ---------------------------------------------------------------------------


def test_post_digest_issue_off_makes_no_gh_call(spy, net_off, capsys) -> None:
    assert post_digest_issue(digest=_digest()) is None
    assert spy.gh_calls == []
    assert "network off" in capsys.readouterr().out


def test_post_digest_issue_off_ignores_injected_run(net_off) -> None:
    """The gate must precede ``_run`` resolution, injected runner included."""
    calls: List[list] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    assert post_digest_issue(digest=_digest(), _run=fake_run) is None
    assert calls == []


def test_post_digest_issue_on_still_calls_gh(net_on) -> None:
    calls: List[list] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout="https://github.com/x/y/issues/7"
        )

    assert post_digest_issue(digest=_digest(), _run=fake_run) == 7
    assert calls and calls[0][0] == "gh"


# ---------------------------------------------------------------------------
# labels.ensure_label_exists
# ---------------------------------------------------------------------------


def test_ensure_label_exists_off_returns_false(spy, net_off) -> None:
    assert labels_mod.ensure_label_exists() is False
    assert spy.gh_calls == []


def test_ensure_label_exists_on_calls_gh(spy, net_on) -> None:
    assert labels_mod.ensure_label_exists() is True
    assert spy.gh_calls and spy.gh_calls[0][:3] == ["gh", "label", "create"]


# ---------------------------------------------------------------------------
# outcome_poller.poll_outcomes
# ---------------------------------------------------------------------------


def test_poll_outcomes_off_returns_zero(spy, net_off, tmp_path: Path) -> None:
    assert outcome_poller.poll_outcomes(vault_root=tmp_path) == 0
    assert spy.gh_calls == []


def test_poll_outcomes_on_lists_via_gh(monkeypatch, net_on, tmp_path: Path) -> None:
    calls: List[list] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(list(cmd), 0, stdout="[]", stderr="")

    monkeypatch.setattr(outcome_poller.subprocess, "run", fake_run)
    assert outcome_poller.poll_outcomes(vault_root=tmp_path) == 0
    assert [c for c in calls if c[0] == "gh"]


# ---------------------------------------------------------------------------
# _gh network backstops
# ---------------------------------------------------------------------------


def test_gh_push_branch_off_returns_false(spy, net_off, tmp_path: Path) -> None:
    assert _gh.push_branch("br", repo_root=tmp_path) is False
    assert spy.calls == []


def test_gh_open_pr_off_returns_none(spy, net_off, tmp_path: Path) -> None:
    result = _gh.open_pr(
        branch="br", title="t", body="b", labels=[], draft=True, repo_root=tmp_path
    )
    assert result is None
    assert spy.calls == []


def test_gh_open_issue_off_returns_none(spy, net_off, tmp_path: Path) -> None:
    assert _gh.open_issue(title="t", body="b", labels=[], repo_root=tmp_path) is None
    assert spy.calls == []


def test_gh_push_branch_on_pushes(spy, net_on, tmp_path: Path) -> None:
    assert _gh.push_branch("br", repo_root=tmp_path) is True
    assert spy.calls and spy.calls[0][:2] == ["git", "push"]


def test_gh_open_pr_on_calls_gh(monkeypatch, net_on, tmp_path: Path) -> None:
    s = ProcessSpy(returncode=0, stdout="42")
    monkeypatch.setattr(subprocess, "run", s)
    result = _gh.open_pr(
        branch="br", title="t", body="b", labels=[], draft=True, repo_root=tmp_path
    )
    assert result == 42
    assert s.gh_calls and s.gh_calls[0][:3] == ["gh", "pr", "create"]


def test_gh_open_issue_on_calls_gh(monkeypatch, net_on, tmp_path: Path) -> None:
    s = ProcessSpy(returncode=0, stdout="https://github.com/x/y/issues/9")
    monkeypatch.setattr(subprocess, "run", s)
    assert _gh.open_issue(title="t", body="b", labels=[], repo_root=tmp_path) == 9
    assert s.gh_calls and s.gh_calls[0][:3] == ["gh", "issue", "create"]


# ---------------------------------------------------------------------------
# telemetry_doctor.open_telemetry_fix_pr
# ---------------------------------------------------------------------------


def _anomalies():
    from mnemo.autopilot.selffix.telemetry_doctor import TelemetryAnomaly

    return [TelemetryAnomaly(kind="cost_usd_always_zero", detail="d", affected_count=5)]


def test_telemetry_doctor_off_opens_no_issue(spy, net_off, tmp_path: Path) -> None:
    from mnemo.autopilot.selffix import telemetry_doctor

    with patch.object(telemetry_doctor._gh, "open_issue") as mock_issue:
        result = telemetry_doctor.open_telemetry_fix_pr(
            _anomalies(), vault_root=tmp_path, repo_root=tmp_path
        )
    assert result is None
    mock_issue.assert_not_called()
    assert spy.gh_calls == []


def test_telemetry_doctor_on_opens_issue(net_on, tmp_path: Path) -> None:
    from mnemo.autopilot.selffix import telemetry_doctor

    with patch.object(
        telemetry_doctor._gh, "open_issue", return_value=11
    ) as mock_issue:
        result = telemetry_doctor.open_telemetry_fix_pr(
            _anomalies(), vault_root=tmp_path, repo_root=tmp_path
        )
    assert result == 11
    mock_issue.assert_called_once()


# ---------------------------------------------------------------------------
# doctor_fixer.open_doctor_fix_pr — cures apply, PR does not
# ---------------------------------------------------------------------------


def _fixable_warning(tmp_path: Path):
    """A rule with one dead source and one live one → a fixable warning."""
    from mnemo.autopilot.selffix.doctor_fixer import detect_fixable

    keep = tmp_path / "briefings" / "keep.md"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("x", encoding="utf-8")

    shared = tmp_path / "shared" / "feedback"
    shared.mkdir(parents=True, exist_ok=True)
    rule = shared / "r.md"
    rule.write_text(
        "---\n"
        "type: feedback\n"
        "tags:\n  - test\n"
        "sources:\n"
        "  - briefings/keep.md\n"
        "  - briefings/gone.md\n"
        "---\n" + "x" * 60 + "\n",
        encoding="utf-8",
    )
    warnings = detect_fixable(vault_root=tmp_path)
    assert warnings, "fixture must produce a fixable warning"
    return warnings, rule


def test_doctor_fix_off_applies_cure_but_opens_no_pr(
    net_off, tmp_path: Path, capsys
) -> None:
    from mnemo.autopilot.selffix import doctor_fixer

    repo = _git_vault(tmp_path)
    warnings, rule = _fixable_warning(repo)

    with patch.object(doctor_fixer._gh, "create_worktree") as mock_wt:
        result = doctor_fixer.open_doctor_fix_pr(
            warnings, vault_root=repo, repo_root=repo
        )

    assert result is None
    mock_wt.assert_not_called()
    # the cure IS applied on disk
    assert "briefings/gone.md" not in rule.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "network off" in out
    assert "no PR opened" in out


def test_doctor_fix_on_reaches_worktree(net_on, tmp_path: Path) -> None:
    from mnemo.autopilot.selffix import doctor_fixer

    repo = _git_vault(tmp_path)
    warnings, _rule = _fixable_warning(repo)

    with patch.object(
        doctor_fixer._gh, "create_worktree", return_value=repo / "wt"
    ) as mock_wt, patch.object(doctor_fixer._gh, "mirror_paths"), patch.object(
        doctor_fixer._gh, "commit_all", return_value=True
    ), patch.object(
        doctor_fixer._gh, "remove_worktree"
    ), patch.object(
        doctor_fixer._gh, "push_branch", return_value=True
    ), patch.object(
        doctor_fixer._gh, "open_pr", return_value=77
    ), patch.object(
        doctor_fixer, "_run_pytest", return_value=True
    ):
        result = doctor_fixer.open_doctor_fix_pr(
            warnings, vault_root=repo, repo_root=repo
        )

    assert result == 77
    mock_wt.assert_called_once()


# ---------------------------------------------------------------------------
# dead_rule_sweep.open_dead_rule_pr — archive applies, PR does not
# ---------------------------------------------------------------------------


def _dead_rule(tmp_path: Path):
    from mnemo.autopilot.selffix.dead_rule_sweep import DeadRule

    shared = tmp_path / "shared" / "feedback"
    shared.mkdir(parents=True, exist_ok=True)
    rule = shared / "dead.md"
    rule.write_text(
        "---\ntype: feedback\ntags:\n  - test\n---\n" + "x" * 60 + "\n",
        encoding="utf-8",
    )
    return [DeadRule(slug="dead", rule_path=rule, last_seen_days=900)], rule


def test_dead_rule_sweep_off_archives_but_opens_no_pr(
    net_off, tmp_path: Path, capsys
) -> None:
    from mnemo.autopilot.selffix import dead_rule_sweep

    repo = _git_vault(tmp_path)
    rules, rule = _dead_rule(repo)

    with patch.object(dead_rule_sweep._gh, "create_worktree") as mock_wt:
        result = dead_rule_sweep.open_dead_rule_pr(
            rules, vault_root=repo, repo_root=repo
        )

    assert result is None
    mock_wt.assert_not_called()
    # the archive IS applied on disk
    assert not rule.exists()
    assert list((repo / "shared" / "_archive").rglob("dead.md"))
    out = capsys.readouterr().out
    assert "network off" in out
    assert "no PR opened" in out


def test_dead_rule_sweep_on_reaches_worktree(net_on, tmp_path: Path) -> None:
    from mnemo.autopilot.selffix import dead_rule_sweep

    repo = _git_vault(tmp_path)
    rules, _rule = _dead_rule(repo)

    with patch.object(
        dead_rule_sweep._gh, "create_worktree", return_value=repo / "wt"
    ) as mock_wt, patch.object(dead_rule_sweep._gh, "mirror_paths"), patch.object(
        dead_rule_sweep._gh, "commit_all", return_value=True
    ), patch.object(
        dead_rule_sweep._gh, "remove_worktree"
    ), patch.object(
        dead_rule_sweep._gh, "push_branch", return_value=True
    ), patch.object(
        dead_rule_sweep._gh, "open_pr", return_value=88
    ), patch.object(
        dead_rule_sweep, "_run_pytest", return_value=True
    ):
        result = dead_rule_sweep.open_dead_rule_pr(
            rules, vault_root=repo, repo_root=repo
        )

    assert result == 88
    mock_wt.assert_called_once()


# ---------------------------------------------------------------------------
# The default config really is off
# ---------------------------------------------------------------------------


def test_default_config_is_network_off(monkeypatch) -> None:
    from mnemo.core import config as config_mod

    monkeypatch.setattr(config_mod, "load_config", lambda *a, **kw: {})
    assert network.enabled() is False
