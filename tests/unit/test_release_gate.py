"""The release refuses to publish a commit whose CI is not green (#594).

A tag on a commit whose CI is red, or never ran, used to ship to PyPI, npm and
the plugin anyway: ``release.yml`` checked the version and the changelog, never
the CI result. ``tools/release_gate.py`` reads the CI workflow's check runs on
the tagged SHA through ``mnemo.core.sessions.check_runs`` (#575), so a job a
rerun cleared passes and a job GitHub never started fails with its own reason.

Every payload here is synthetic, in the shape ``gh api`` returns with the
``--jq`` filters the gate passes; nothing reaches the network.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

from tools import release_gate as rg

REPO = "owner/name"
SHA = "a" * 40
SUITE = 7001
BILLING = ("The job was not started because recent account payments have failed "
           "or your spending limit needs to be increased.")


def ci_run(status: str = "completed", conclusion: Optional[str] = "success",
           suite: int = SUITE, path: str = rg.CI_WORKFLOW) -> Dict[str, Any]:
    return {"id": 900 + suite, "path": path, "status": status,
            "conclusion": conclusion, "check_suite_id": suite}


def check(name: str, conclusion: Optional[str] = "success", *, cid: int = 0,
          status: str = "completed", suite: int = SUITE) -> Dict[str, Any]:
    return {"id": cid or abs(hash((name, conclusion, cid))) % 10**6, "name": name,
            "status": status, "conclusion": conclusion, "suite": suite}


GREEN = [check("ubuntu-latest / py3.8"), check("windows-latest / py3.11"),
         check("npm wrapper (node 20)")]


class FakeGitHub:
    """Answers the three ``gh api`` calls the gate makes, one state per poll."""

    def __init__(self, polls: Sequence[Tuple[Optional[list], Optional[list]]],
                 notes: Optional[Dict[int, List[str]]] = None):
        self.polls = list(polls)
        self.notes = notes or {}
        self.calls: List[List[str]] = []
        self._i = -1

    def __call__(self, argv: Sequence[str], _stdin: Optional[str]) -> Tuple[int, str, str]:
        argv = list(argv)
        self.calls.append(argv)
        url = argv[2]
        if "/actions/runs?" in url:
            self._i = min(self._i + 1, len(self.polls) - 1)
            payload = self.polls[self._i][0]
        elif "/check-runs/" in url and url.endswith("/annotations"):
            payload = self.notes.get(int(url.split("/")[-2]), [])
        elif "/check-runs?" in url:
            payload = self.polls[self._i][1]
        else:  # pragma: no cover - the gate asked something unexpected
            raise AssertionError(argv)
        if payload is None:
            return 1, "", "HTTP 502"
        return 0, json.dumps(payload), ""


def judge(runs: Optional[list], checks: Optional[list],
          notes: Optional[Dict[int, List[str]]] = None) -> Tuple[str, str]:
    return rg.judge(REPO, SHA, run=FakeGitHub([(runs, checks)], notes))


# --- the verdict on one reading -------------------------------------------

def test_all_green_passes():
    state, why = judge([ci_run()], GREEN)
    assert state == rg.PASS, why


def test_one_red_job_fails_and_names_it():
    state, why = judge([ci_run(conclusion="failure")],
                       GREEN + [check("macos-latest / py3.9", "failure")])
    assert state == rg.FAIL
    assert "macos-latest / py3.9" in why
    assert "never" not in why


def test_a_job_github_never_started_fails_with_its_own_message():
    """Billing lapsed: every job fails in seconds and no code was tested."""
    red = check("ubuntu-latest / py3.8", "failure", cid=55)
    state, why = judge([ci_run(conclusion="failure")], [red], notes={55: [BILLING]})
    assert state == rg.FAIL
    assert "never ran" in why and "ubuntu-latest / py3.8" in why
    plain = judge([ci_run(conclusion="failure")], [check("ubuntu-latest / py3.8", "failure")])[1]
    assert why != plain, "never-run must not read like a red test"


def test_a_failure_a_rerun_cleared_passes():
    runs = [check("windows-latest / py3.11", "failure", cid=1),
            check("windows-latest / py3.11", "success", cid=2),
            check("ubuntu-latest / py3.8")]
    state, why = judge([ci_run()], runs)
    assert state == rg.PASS, why


def test_ci_still_running_is_pending():
    state, _ = judge([ci_run(status="in_progress", conclusion=None)],
                     [check("ubuntu-latest / py3.8", None, status="in_progress")])
    assert state == rg.PENDING


def test_ci_queued_with_no_jobs_yet_is_pending():
    assert judge([ci_run(status="queued", conclusion=None)], [])[0] == rg.PENDING


def test_no_ci_run_for_the_sha_fails():
    state, why = judge([], [])
    assert state == rg.FAIL
    assert "no CI run" in why


def test_only_other_workflows_count_as_no_ci_run():
    """The release workflow's own jobs sit on the same SHA, still running."""
    release = ci_run(status="in_progress", conclusion=None, suite=8001,
                     path=".github/workflows/release.yml")
    state, why = judge([release], [check("Release gate", None, status="in_progress", suite=8001)])
    assert state == rg.FAIL and "no CI run" in why


def test_check_runs_outside_the_ci_suite_are_ignored():
    """A red job in another workflow's suite says nothing about CI."""
    other = check("Publish to PyPI", "failure", suite=8001)
    assert judge([ci_run()], GREEN + [other])[0] == rg.PASS


def test_a_completed_ci_run_with_no_jobs_fails():
    """A workflow that could not start (bad YAML) concludes with no job at all."""
    state, why = judge([ci_run(conclusion="startup_failure")], [])
    assert state == rg.FAIL
    assert "startup_failure" in why


def test_a_skipped_job_is_not_green():
    state, why = judge([ci_run()], GREEN + [check("binary-canary", "skipped")])
    assert state == rg.FAIL and "binary-canary" in why


def test_github_not_reached_is_pending_not_a_verdict():
    assert judge(None, None)[0] == rg.PENDING
    assert judge([ci_run()], None)[0] == rg.PENDING


def test_unreadable_annotations_count_as_a_real_failure():
    """Same rule as pr-follow: when GitHub could not say why, it is red."""
    class NoNotes(FakeGitHub):
        def __call__(self, argv, stdin):
            if str(argv[2]).endswith("/annotations"):
                return 1, "", "HTTP 502"
            return super().__call__(argv, stdin)

    gh = NoNotes([([ci_run(conclusion="failure")], [check("x", "failure")])])
    state, why = rg.judge(REPO, SHA, run=gh)
    assert state == rg.FAIL and "never ran" not in why


def test_it_asks_for_the_tagged_sha():
    gh = FakeGitHub([([ci_run()], GREEN)])
    rg.judge(REPO, SHA, run=gh)
    assert all(SHA in c[2] for c in gh.calls if "annotations" not in c[2])


# --- waiting for CI that is still running -----------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: List[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s


def test_it_waits_for_ci_to_finish_then_passes():
    running = ([ci_run(status="in_progress", conclusion=None)], [])
    gh = FakeGitHub([running, running, ([ci_run()], GREEN)])
    clock = Clock()
    ok, why = rg.wait(REPO, SHA, run=gh, clock=clock, sleep=clock.sleep,
                      timeout=600, interval=30)
    assert ok, why
    assert clock.slept == [30, 30]


def test_it_waits_then_fails_when_ci_finishes_red():
    running = ([ci_run(status="in_progress", conclusion=None)], [])
    gh = FakeGitHub([running, ([ci_run(conclusion="failure")], [check("x", "failure")])])
    clock = Clock()
    ok, _ = rg.wait(REPO, SHA, run=gh, clock=clock, sleep=clock.sleep, timeout=600, interval=30)
    assert not ok


def test_the_wait_is_bounded_and_says_to_rerun_the_release():
    gh = FakeGitHub([([ci_run(status="in_progress", conclusion=None)], [])])
    clock = Clock()
    ok, why = rg.wait(REPO, SHA, run=gh, clock=clock, sleep=clock.sleep,
                      timeout=120, interval=30)
    assert not ok
    assert clock.now <= 120
    assert "still running" in why and "re-run" in why.lower()


def test_a_verdict_does_not_wait():
    gh = FakeGitHub([([], [])])
    clock = Clock()
    ok, _ = rg.wait(REPO, SHA, run=gh, clock=clock, sleep=clock.sleep, timeout=600, interval=30)
    assert not ok and clock.slept == []


# --- the command line the workflow calls ------------------------------------

@pytest.mark.parametrize("polls,code", [
    ([([ci_run()], GREEN)], 0),
    ([([ci_run(conclusion="failure")], [check("x", "failure")])], 1),
    ([([], [])], 1),
])
def test_main_exit_code(polls, code, capsys):
    gh = FakeGitHub(polls)
    assert rg.main(["--repo", REPO, "--sha", SHA], run=gh, sleep=lambda s: None) == code
    out = capsys.readouterr().out
    if code:
        assert "::error::" in out


def test_main_reads_repo_and_sha_from_the_actions_environment(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_SHA", SHA)
    gh = FakeGitHub([([ci_run()], GREEN)])
    assert rg.main([], run=gh, sleep=lambda s: None) == 0
    assert REPO in gh.calls[0][2] and SHA in gh.calls[0][2]
