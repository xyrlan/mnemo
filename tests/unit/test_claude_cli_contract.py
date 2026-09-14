"""The ``claude`` CLI contract module: the statement, the parser, the failure.

These tests pin what :mod:`mnemo.core.claude_cli` *says* on a miss and how it
reads the jobs directory. They do not, and cannot, prove the assumptions hold
against the installed binary — that is ``tests/live/test_claude_cli_live.py``,
opt-in, which spawns a real session. The fixtures below are byte-for-byte
copies of real ``--bg`` output (#211); the live test is what keeps them
honest.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from mnemo.core import claude_cli

REAL_BG_STDOUT = (
    "backgrounded · \x1b[36ma1b2c3d4\x1b[39m\n"
    "\x1b[2m  claude agents             list sessions\x1b[22m\n"
    "\x1b[2m  claude attach a1b2c3d4    open in this terminal\x1b[22m\n"
    "\x1b[2m  claude logs a1b2c3d4      show recent output\x1b[22m\n"
    "\x1b[2m  claude stop a1b2c3d4      stop this session\x1b[22m\n"
)


# --- the statement ---------------------------------------------------------


def test_every_assumption_is_uniquely_keyed_and_dated() -> None:
    keys = [a.key for a in claude_cli.ASSUMPTIONS]
    assert len(keys) == len(set(keys))
    for a in claude_cli.ASSUMPTIONS:
        assert re.search(r"\d+\.\d+\.\d+ on \d{4}-\d{2}-\d{2}", a.verified), a.key
        assert a.claim and a.used_by, a.key


def test_verified_against_is_a_version_triple() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", claude_cli.VERIFIED_AGAINST)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", claude_cli.VERIFIED_ON)


def test_the_assumptions_the_code_cites_exist() -> None:
    """A key cited by a failure path must resolve, or the failure itself fails."""
    for key in ("bg-prints-short-id", "jobs-state-json"):
        assert claude_cli.assumption(key).key == key
    with pytest.raises(KeyError):
        claude_cli.assumption("no-such-assumption")


# --- the version -----------------------------------------------------------


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("2.1.270 (Claude Code)\n", "2.1.270"),
        ("2.1.270", "2.1.270"),
        ("Claude Code v10.0.1-beta", "10.0.1"),
        ("", None),
        ("no version here", None),
    ],
)
def test_parse_version(stdout: str, expected: str | None) -> None:
    assert claude_cli.parse_version(stdout) == expected


def test_claude_version_is_none_when_the_binary_is_missing(monkeypatch) -> None:
    def boom(*a, **k):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(claude_cli.subprocess, "run", boom)
    assert claude_cli.claude_version() is None


def test_claude_version_is_none_on_a_nonzero_exit(monkeypatch) -> None:
    monkeypatch.setattr(
        claude_cli.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="2.1.270", stderr="x"),
    )
    assert claude_cli.claude_version() is None


# --- the id parser ---------------------------------------------------------


def test_short_id_is_found_under_its_colour() -> None:
    assert claude_cli.short_id_from(REAL_BG_STDOUT) == "a1b2c3d4"


def test_a_commit_sha_is_not_truncated_into_an_id() -> None:
    sha = "a1b2c3d4" + "e" * 32
    assert claude_cli.short_id_from(f"{sha}\nbackgrounded · deadbeef\n") == "deadbeef"


def test_require_short_id_names_the_assumption_and_the_version(monkeypatch) -> None:
    """The loud failure #235 asks for: which assumption, against which version."""
    monkeypatch.setattr(claude_cli, "claude_version", lambda: "9.9.9")

    with pytest.raises(claude_cli.ContractBroken) as info:
        claude_cli.require_short_id("Started your session in the background.\n")

    message = str(info.value)
    assert "bg-prints-short-id" in message
    assert "installed claude 9.9.9" in message
    assert claude_cli.assumption("bg-prints-short-id").verified in message
    # The bytes are quoted, so a colour change is visible in the report.
    assert "'Started your session in the background.'" in message
    assert info.value.assumption.key == "bg-prints-short-id"


def test_require_short_id_says_so_when_nothing_was_printed(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli, "claude_version", lambda: None)
    with pytest.raises(claude_cli.ContractBroken) as info:
        claude_cli.require_short_id("")
    assert "printed nothing" in str(info.value)
    assert "unknown" in str(info.value)  # the version probe failed; say so


def test_require_short_id_quotes_escapes_visibly(monkeypatch) -> None:
    """An id that is there but mis-shaped must be shown with its escapes."""
    monkeypatch.setattr(claude_cli, "claude_version", lambda: "9.9.9")
    with pytest.raises(claude_cli.ContractBroken) as info:
        claude_cli.require_short_id("backgrounded · \x1b[36mA1B2C3D4\x1b[39m\n")
    assert "\\x1b[36m" in str(info.value)


# --- the jobs directory ----------------------------------------------------


def _register(jobs: Path, short_id: str, **fields) -> Path:
    entry = jobs / short_id
    entry.mkdir()
    (entry / "state.json").write_text(json.dumps(fields), encoding="utf-8")
    return entry


def test_a_registered_child_passes(tmp_jobs_dir: Path, tmp_path: Path) -> None:
    tree = tmp_path / "proj-wt-1"
    tree.mkdir()
    _register(tmp_jobs_dir, "a1b2c3d4", cwd=str(tree), state="working", tempo="active")
    assert claude_cli.verify_registered("a1b2c3d4", cwd=tree) is None


def test_cwd_is_compared_through_realpath(tmp_jobs_dir: Path, tmp_path: Path) -> None:
    """A worktree reached through a symlink is the same tree, not a mismatch."""
    real = tmp_path / "real-wt-1"
    real.mkdir()
    link = tmp_path / "link-wt-1"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows without privilege
        pytest.skip("symlinks unavailable")
    _register(tmp_jobs_dir, "a1b2c3d4", cwd=str(real))
    assert claude_cli.verify_registered("a1b2c3d4", cwd=link) is None


def test_a_missing_entry_names_the_jobs_assumption(
    tmp_jobs_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(claude_cli, "claude_version", lambda: "9.9.9")
    message = claude_cli.verify_registered("a1b2c3d4", cwd=tmp_path)
    assert message is not None
    assert "jobs-state-json" in message
    assert "a1b2c3d4" in message
    assert "installed claude 9.9.9" in message


def test_a_mismatched_cwd_is_reported_with_both_paths(
    tmp_jobs_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(claude_cli, "claude_version", lambda: "9.9.9")
    other = tmp_path / "elsewhere"
    other.mkdir()
    _register(tmp_jobs_dir, "a1b2c3d4", cwd=str(other))
    message = claude_cli.verify_registered("a1b2c3d4", cwd=tmp_path / "proj-wt-1")
    assert message is not None
    assert "elsewhere" in message and "proj-wt-1" in message


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        pytest.param("{not json", "not valid JSON", id="malformed"),
        pytest.param("[1, 2]", "not an object", id="list"),
        pytest.param('{"state": "working"}', "no `cwd`", id="no-cwd"),
    ],
)
def test_an_unusable_state_file_says_what_is_wrong_with_it(
    tmp_jobs_dir: Path, tmp_path: Path, monkeypatch, body: str, fragment: str
) -> None:
    monkeypatch.setattr(claude_cli, "claude_version", lambda: "9.9.9")
    entry = tmp_jobs_dir / "a1b2c3d4"
    entry.mkdir()
    (entry / "state.json").write_text(body, encoding="utf-8")
    message = claude_cli.verify_registered("a1b2c3d4", cwd=tmp_path)
    assert message is not None
    assert fragment in message
