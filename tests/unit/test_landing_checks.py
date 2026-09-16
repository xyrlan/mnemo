"""The merge gate reads individual checks, never the rollup (2026-09-16)."""
import json

from mnemo.core import landing


def _fake_gh(payload, *, returncode=0):
    class _R:
        pass

    def run(args, **kwargs):
        r = _R()
        r.returncode = returncode
        r.stdout = json.dumps(payload)
        r.stderr = ""
        return r

    return run


def test_no_failing_checks_when_all_pass(monkeypatch):
    monkeypatch.setattr(landing, "_run_gh", _fake_gh([
        {"name": "ubuntu / py3.12", "bucket": "pass", "state": "SUCCESS"},
        {"name": "lint", "bucket": "pass", "state": "SUCCESS"},
    ]))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == []


def test_a_failing_check_is_named(monkeypatch):
    monkeypatch.setattr(landing, "_run_gh", _fake_gh([
        {"name": "ubuntu / py3.12", "bucket": "pass", "state": "SUCCESS"},
        {"name": "windows / py3.11", "bucket": "fail", "state": "FAILURE"},
    ]))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == [
        "windows / py3.11"
    ]


def test_a_non_blocking_failure_is_still_a_failure(monkeypatch):
    """The whole point: a job the repo marked non-blocking fails while the
    run's conclusion and the PR's rollup both report success."""
    monkeypatch.setattr(landing, "_run_gh", _fake_gh([
        {"name": "required", "bucket": "pass", "state": "SUCCESS"},
        {"name": "optional-job", "bucket": "fail", "state": "FAILURE"},
    ]))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == [
        "optional-job"
    ]


def test_pending_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(landing, "_run_gh", _fake_gh([
        {"name": "slow", "bucket": "pending", "state": "IN_PROGRESS"},
    ]))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == []


def test_skipped_and_cancelled_are_not_failures(monkeypatch):
    monkeypatch.setattr(landing, "_run_gh", _fake_gh([
        {"name": "a", "bucket": "skipping", "state": "SKIPPED"},
        {"name": "b", "bucket": "cancel", "state": "CANCELLED"},
    ]))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == []


def test_unreadable_checks_are_not_invented(monkeypatch):
    """gh unavailable, or a PR with no checks at all: report nothing rather
    than blocking a landing on an answer we do not have."""
    monkeypatch.setattr(landing, "_run_gh", _fake_gh({}, returncode=1))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == []


def test_malformed_json_is_not_a_failure(monkeypatch):
    def run(args, **kwargs):
        class _R:
            returncode = 0
            stdout = "not json"
            stderr = ""
        return _R()

    monkeypatch.setattr(landing, "_run_gh", run)

    assert landing.failing_checks("https://github.com/o/r/pull/1") == []


def test_gh_missing_is_not_a_failure(monkeypatch):
    """An exception from the subprocess layer refuses nothing."""

    def run(args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(landing, "_run_gh", run)

    assert landing.failing_checks("https://github.com/o/r/pull/1") == []


def test_pending_exit_code_still_reads_the_rows(monkeypatch):
    """`gh pr checks` exits 8 while checks are pending — the rows are still
    there, and a check that already failed among them is still a failure."""
    monkeypatch.setattr(landing, "_run_gh", _fake_gh([
        {"name": "slow", "bucket": "pending", "state": "IN_PROGRESS"},
        {"name": "windows", "bucket": "fail", "state": "FAILURE"},
    ], returncode=8))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == ["windows"]
