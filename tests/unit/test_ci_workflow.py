"""The CI test matrix: what a PR and a push to master ask runners for (#692).

GitHub runs at most 5 macOS jobs at a time per account. Every PR asked for 5
and every push to master for 5 more, so with 7 PRs open a macOS job queued
for 75 minutes. Each job also ran the suite twice. These tests pin the shape
that fixed both, by reading ``ci.yml`` and expanding its matrix per event.
"""
from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import Dict, List

import yaml  # dev dependency; these tests must never silently skip

REPO = Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"

PR_GUARD = "${{ github.event_name == 'pull_request' && fromJSON('"


def _test_job() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["test"]


def _expand(event: str) -> List[Dict[str, str]]:
    """The `test` job's matrix as GitHub expands it for ``event``.

    Only the one expression shape ci.yml uses is understood:
    ``event == 'pull_request' && fromJSON('<list>') || fromJSON('[]')``.
    Anything else fails here rather than being guessed at.
    """
    matrix = _test_job()["strategy"]["matrix"]
    exclude = matrix.get("exclude", [])
    if isinstance(exclude, str):
        m = re.fullmatch(
            re.escape(PR_GUARD) + r"(.*?)'\) \|\| fromJSON\('\[\]'\) }}",
            exclude.strip(),
        )
        assert m, f"unrecognised exclude expression: {exclude!r}"
        exclude = json.loads(m.group(1)) if event == "pull_request" else []
    rows = [
        {"os": o, "python-version": v}
        for o, v in itertools.product(matrix["os"], matrix["python-version"])
    ]
    return [r for r in rows if r not in exclude]


def _versions(event: str, os_name: str) -> List[str]:
    return [r["python-version"] for r in _expand(event) if r["os"] == os_name]


def test_a_pr_asks_for_two_macos_jobs_at_the_ends_of_the_range():
    assert _versions("pull_request", "macos-latest") == ["3.8", "3.12"]


def test_a_push_to_master_keeps_the_full_macos_matrix():
    assert _versions("push", "macos-latest") == ["3.8", "3.9", "3.10", "3.11", "3.12"]


def test_ubuntu_keeps_all_five_versions_on_both_events():
    for event in ("pull_request", "push"):
        assert _versions(event, "ubuntu-latest") == ["3.8", "3.9", "3.10", "3.11", "3.12"]


def test_the_ends_of_the_matrix_are_the_supported_range():
    """Trimming to "the ends" is only right while they are the real floor and ceiling."""
    versions = _test_job()["strategy"]["matrix"]["python-version"]
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.8"' in pyproject
    assert versions[0] == "3.8"


def test_each_job_runs_the_suite_once_and_gates_coverage():
    steps = _test_job()["steps"]
    runs = [s["run"] for s in steps if "pytest" in s.get("run", "")]
    assert len(runs) == 1, f"the suite must run once per job, got {len(runs)} pytest steps"
    (run,) = runs
    assert "--cov-fail-under=85" in run
    assert run.rstrip().endswith("tests/"), "the one run must collect all of tests/"
    step = next(s for s in steps if s.get("run") == run)
    # Without it a child process that starts its own coverage outside the
    # repo falls back to statement-only data and aborts the combine.
    assert "COVERAGE_RCFILE" in step.get("env", {})
