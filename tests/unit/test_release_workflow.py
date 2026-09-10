"""Release ordering: fallible work before irreversible work.

Publishing to PyPI or npm is irreversible — a version number, once taken, can
never be reused. Building four platform binaries is the fallible step. If the
publish jobs run first, a build failure leaves the registries advertising a
version whose release has no binaries, which is exactly what the plugin
install needs.

That is not hypothetical: it happened on v0.17.0. PyPI and npm both published,
then the Windows job failed on a missing `shasum`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # dev dependency; these tests must never silently skip

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


@pytest.fixture(scope="module")
def jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def _needs(job: dict) -> set[str]:
    n = job.get("needs", [])
    return {n} if isinstance(n, str) else set(n)


def _depends_on(jobs: dict, name: str, target: str) -> bool:
    """True when `name` reaches `target` through any chain of needs."""
    seen, stack = set(), list(_needs(jobs[name]))
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur in seen or cur not in jobs:
            continue
        seen.add(cur)
        stack.extend(_needs(jobs[cur]))
    return False


@pytest.mark.parametrize("publisher", ["publish-pypi", "publish-npm"])
def test_publishing_is_gated_behind_the_binary_build(jobs: dict, publisher: str):
    assert _depends_on(jobs, publisher, "build-binaries"), (
        f"{publisher} can publish before the binaries build — an irreversible "
        "step ahead of a fallible one"
    )


def test_binaries_are_attached_only_after_they_all_build(jobs: dict):
    assert _depends_on(jobs, "publish-binaries", "build-binaries")


def test_the_matrix_covers_every_platform_the_launcher_asks_for(jobs: dict):
    """bin/launch derives these target names; a gap means a silent no-op."""
    targets = {m["target"] for m in jobs["build-binaries"]["strategy"]["matrix"]["include"]}
    assert targets == {"darwin-arm64", "darwin-x64", "linux-x64", "win-x64"}


def test_no_platform_is_allowed_to_fail_quietly(jobs: dict):
    """A partial binary set is worse than none: some users get a silent no-op."""
    strategy = jobs["build-binaries"]["strategy"]
    assert strategy.get("fail-fast") is False, "let every platform report, don't cancel siblings"
    assert "continue-on-error" not in jobs["build-binaries"]


def test_a_manual_run_can_never_publish():
    """workflow_dispatch exists to test the build without burning a version.

    Every publishing job must therefore be guarded on the ref being a tag —
    otherwise triggering a test build would release from a branch.
    """
    wf = yaml.safe_load(WORKFLOW.read_text())
    triggers = wf[True] if True in wf else wf["on"]
    assert "workflow_dispatch" in triggers

    for name in ("publish-pypi", "publish-npm", "publish-binaries"):
        guard = wf["jobs"][name].get("if", "")
        assert "refs/tags/v" in guard, f"{name} would run on a manual dispatch"


def test_the_build_job_is_not_tag_guarded():
    """It is the whole point of a manual run."""
    wf = yaml.safe_load(WORKFLOW.read_text())
    assert "if" not in wf["jobs"]["build-binaries"]


def test_checksums_are_generated_without_shasum(jobs: dict):
    """Neither shasum nor sha256sum is guaranteed in Git Bash on Windows."""
    package = next(
        s for s in jobs["build-binaries"]["steps"] if s.get("name") == "Package"
    )
    # Comments are allowed to name them — they explain why they aren't used.
    code = "\n".join(
        ln for ln in package["run"].splitlines() if not ln.strip().startswith("#")
    )
    assert "shasum" not in code
    assert "sha256sum" not in code
    assert "hashlib" in code


def test_binary_build_pipes_stdout_before_publishing():
    """A console stdout is not the shape Claude Code runs commands in.

    `mnemo doctor` shipped dead on Windows through four releases: Claude Code
    pipes a slash command's stdout, Python then picks cp1252 rather than the
    console codepage, and the first `→` raised UnicodeEncodeError. The
    existing smoke test missed it because it ran the binary on a terminal.
    Redirecting to a file is what reproduces it.
    """
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    steps = jobs["build-binaries"]["steps"]
    piped = [s for s in steps if "piped" in str(s.get("name", "")).lower()]
    assert piped, "the binary build must exercise a piped stdout before publishing"
    body = piped[0]["run"]
    assert "> out.txt" in body, "stdout must actually be redirected, not shown on a tty"
    assert "UnicodeEncodeError" in body, "the guard must fail on an encoding crash"
    for cmd in ("status", "doctor"):
        assert cmd in body, f"pipe the {cmd} command — both print non-ASCII"


def test_windows_ci_runs_a_command_end_to_end():
    """Unit tests passed on Windows all the way through the doctor outage.

    They never invoke a command end to end, so no assertion could have caught
    it. The CI job needs at least one real run through a pipe.
    """
    ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
    steps = ci["jobs"]["windows-experimental"]["steps"]
    piped = [s for s in steps if "piped" in str(s.get("name", "")).lower()]
    assert piped, "the Windows CI job must run the CLI through a pipe, not only pytest"
    assert "UnicodeEncodeError" in piped[0]["run"]
