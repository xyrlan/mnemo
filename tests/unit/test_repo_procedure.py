"""``CLAUDE.md`` is this repo's procedure, and it has to stay true (#385).

Claude Code attaches the repo's ``CLAUDE.md`` to every session that opens in
it, dispatched children included — measured across 20 clubinho children, all
20 of which received it. That makes it the channel a dispatched child actually
reads, and the reason ``core/dispatch.py`` holds no repo's procedure of its
own.

A channel nobody checks drifts. These tests execute what the file claims
rather than grepping for it, and cap what it may grow into: every session in
this repo pays for every line.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO / "CLAUDE.md"

#: What a child pays for, in lines. Procedure is a short list of boundaries;
#: anything longer is either an approach (which must not be here at all) or a
#: doc that belongs in ``docs/``.
MAX_LINES = 120


def test_the_repo_ships_a_claude_md() -> None:
    assert CLAUDE_MD.is_file(), "the channel a dispatched child reads is missing"


def test_the_documented_test_command_imports_this_checkout() -> None:
    """The #385 case, executed rather than asserted.

    ``PYTHONPATH=src`` has to win over the editable install's absolute path
    entry, or a worktree's suite measures the main checkout's code.
    """
    env = dict(os.environ, PYTHONPATH="src")
    result = subprocess.run(
        [sys.executable, "-c", "import mnemo; print(mnemo.__file__)"],
        cwd=str(REPO), env=env, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    # `startswith` on the resolved strings, not `Path.is_relative_to`, which
    # this package's own 3.8 floor does not have — the trap CLAUDE.md names.
    resolved = str(Path(result.stdout.strip()).resolve())
    assert resolved.startswith(str(REPO.resolve()) + os.sep), (
        f"PYTHONPATH=src imported {resolved}, not this checkout's src/"
    )


def test_claude_md_states_that_command() -> None:
    text = CLAUDE_MD.read_text(encoding="utf-8")

    assert "PYTHONPATH=src python3 -m pytest" in text


def test_claude_md_names_the_python_floor_pyproject_declares() -> None:
    floor = re.search(
        r'requires-python\s*=\s*">=([\d.]+)"',
        (REPO / "pyproject.toml").read_text(encoding="utf-8"),
    )
    assert floor, "pyproject.toml declares no requires-python"

    assert f"Python {floor.group(1)}" in CLAUDE_MD.read_text(encoding="utf-8"), (
        f"CLAUDE.md does not name the {floor.group(1)} floor pyproject declares"
    )


def test_claude_md_sends_changelog_entries_to_the_fragments_directory() -> None:
    text = CLAUDE_MD.read_text(encoding="utf-8")

    assert "changelog.d/" in text and "never in `CHANGELOG.md`" in text


def test_claude_md_stays_short_enough_to_be_read() -> None:
    lines = CLAUDE_MD.read_text(encoding="utf-8").splitlines()

    assert len(lines) <= MAX_LINES, (
        f"CLAUDE.md is {len(lines)} lines; every session in this repo pays for "
        f"each one. Procedure is a short list — move the rest to docs/."
    )


def test_dispatch_hard_codes_no_repo_procedure() -> None:
    """#385's boundary: the dispatcher may state where a repo keeps changelog
    fragments — a directory it can see — and nothing else about any repo."""
    source = (REPO / "src" / "mnemo" / "core" / "dispatch.py").read_text(encoding="utf-8")
    strings = re.findall(r'"""(.*?)"""', source, re.DOTALL) + re.findall(r'"([^"\n]*)"', source)

    for banned in ("PYTHONPATH", "CARGO_TARGET_DIR", "runInBand", "pytest tests/"):
        assert not any(banned in s for s in strings), (
            f"dispatch.py states {banned!r}: one repo's procedure, hard-coded"
        )
