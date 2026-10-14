"""Text read from a subprocess is decoded as UTF-8, whatever the locale (#659).

``subprocess.run(..., text=True)`` without ``encoding=`` decodes the child's
output in the locale's codec: UTF-8 on macOS and Linux, cp1252 on the Windows
runner. ``git``, ``gh`` and ``claude`` write UTF-8 everywhere, so on Windows
``ação`` came back as ``aÃ§Ã£o`` and ``┐`` (UTF-8 ``e2 94 90``; 0x90 has no
cp1252 mapping) raised ``UnicodeDecodeError``, which most call sites either
swallowed into an empty answer or never caught.

These tests drive real ``git`` through two of the call sites #659 lists. On
the Windows runner they run under the native codec; elsewhere
``locale_codec_is_cp1252`` makes the codec that ``text=True`` picks cp1252,
the way Windows does. Either way, a probe first proves the codec in force is
not UTF-8, so the tests cannot pass for the wrong reason.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mnemo.autopilot.proposer._git_signals import git_log_since
from mnemo.core.sessions import delivery

_PROBE = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xc3\\xa9')"]


@pytest.fixture
def locale_codec_is_cp1252(monkeypatch: pytest.MonkeyPatch) -> None:
    """``text=True`` without ``encoding=`` decodes as cp1252, as on Windows."""
    if sys.platform != "win32":
        if not hasattr(subprocess, "_text_encoding"):
            pytest.skip("no hook into subprocess's default codec before Python 3.11")
        monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp1252")
    # The probe writes UTF-8 "é". Under the codec this test is about it does
    # not read back as "é"; if it does, nothing below would bite.
    seen = subprocess.run(_PROBE, capture_output=True, text=True).stdout  # encoding: platform default
    if seen == "é":
        message = "the locale codec is UTF-8 here (PYTHONUTF8 set?): this test would prove nothing"
        if sys.platform == "win32":
            pytest.fail(message)
        pytest.skip(message)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-b", "master"], root)
    _git(["config", "user.email", "t@example.com"], root)
    _git(["config", "user.name", "t"], root)
    _git(["config", "i18n.logOutputEncoding", "utf-8"], root)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(["add", "README.md"], root)
    _git(["commit", "-m", "base"], root)
    return root


@pytest.mark.parametrize("subject", [
    "ação",  # cp1252 reads it as "aÃ§Ã£o": silent mojibake
    "fix ┐ drawing",  # byte 0x90: cp1252 raises, the call swallowed it into []
])
def test_commit_subjects_read_back_as_written(locale_codec_is_cp1252, repo: Path, subject: str) -> None:
    (repo / "work.txt").write_text("x\n", encoding="utf-8")
    _git(["add", "work.txt"], repo)
    _git(["commit", "-m", subject], repo)

    subjects = git_log_since(repo, "1970-01-01T00:00:00Z")

    assert subjects == [subject, "base"]


def test_a_non_ascii_branch_name_reads_back_as_written(locale_codec_is_cp1252, repo: Path) -> None:
    """``deliver`` pushes and opens the PR for the branch this returns."""
    _git(["checkout", "-b", "fix/ação"], repo)

    assert delivery._branch_of(repo) == "fix/ação"
