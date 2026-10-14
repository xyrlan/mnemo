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

import ast
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


# --- the invariant: no call under src/ adds the pattern back ----------------
#
# The same shape as ``test_text_io_encoding.py`` (#255) for files: an AST walk
# that fails on any call passing a literal true ``text=`` or
# ``universal_newlines=`` without ``encoding=``. It keys on the keyword, not
# the callee, so a seam such as ``llm._auth_status_run = subprocess.run`` or
# ``check_output``/``Popen`` is caught too; a dataclass field named ``text``
# is passed a string, never the literal ``True``, and is not.

REPO = Path(__file__).resolve().parents[2]
# A call that means the locale's codec says so on the line of its ``text=``.
EXEMPT_MARKER = "# encoding: platform default"


def bare_text_mode_calls(source: str, filename: str = "<string>") -> list[int]:
    """Lines of calls that decode a subprocess's output in the locale's codec."""
    lines = source.splitlines()
    hits = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        kws = {kw.arg: kw.value for kw in node.keywords}
        flag = kws.get("text", kws.get("universal_newlines"))
        if not (isinstance(flag, ast.Constant) and flag.value is True) or "encoding" in kws:
            continue
        if EXEMPT_MARKER in lines[node.lineno - 1] or EXEMPT_MARKER in lines[flag.lineno - 1]:
            continue
        hits.append(node.lineno)
    return hits


def test_every_text_mode_subprocess_under_src_names_its_encoding() -> None:
    offenders = [
        f"{py.relative_to(REPO)}:{line}"
        for py in sorted((REPO / "src").rglob("*.py"))
        for line in bare_text_mode_calls(py.read_text(encoding="utf-8"), str(py))
    ]
    assert not offenders, (
        f"{len(offenders)} call(s) under src/ pass text=True without encoding=, so "
        "the output is decoded as cp1252 on Windows while git/gh/claude write "
        "UTF-8. Pass encoding=\"utf-8\", errors=\"replace\":\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("snippet, flagged", [
    ("subprocess.run(a, capture_output=True, text=True)", True),
    ("subprocess.run(a, universal_newlines=True)", True),
    ("subprocess.check_output(a, text=True)", True),
    ("subprocess.Popen(a, stdout=PIPE, text=True)", True),
    ("_auth_status_run(a, capture_output=True, text=True)", True),
    ("subprocess.run(a, text=True, encoding='utf-8')", False),
    ("subprocess.run(a, text=True, encoding='utf-8', errors='replace')", False),
    ("subprocess.run(a, capture_output=True)", False),
    ("subprocess.run(a, text=False)", False),
    ("Stall(kind='x', text=wanted)", False),
    ("subprocess.run(a, text=True)  # encoding: platform default", False),
])
def test_scanner_recognises_each_shape(snippet: str, flagged: bool) -> None:
    assert bool(bare_text_mode_calls(snippet)) is flagged
