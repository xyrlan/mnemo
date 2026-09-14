"""``mnemo land`` — the read-only view of a contract, and the merge behind a flag.

The invariant is the one ``deliver`` has, one step later: nothing irreversible
happens from a read. ``mnemo land <contract>`` prints and exits; ``--merge``
merges only after a rehearsal in a throwaway worktree passed in full. So the
tests that matter most are the ones proving that the read merges nothing, and
that a red rehearsal merges nothing either.

Git is real, as in :mod:`tests.unit.test_landing`. ``gh`` is stubbed at the
subprocess boundary, and the merge step is recorded rather than run.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from mnemo.cli.commands import land
from mnemo.core import landing


def _run(args, *, cwd) -> None:
    subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "mnemo"
    root.mkdir()
    _run(["git", "init", "-b", "master"], cwd=root)
    _run(["git", "config", "user.email", "t@example.com"], cwd=root)
    _run(["git", "config", "user.name", "t"], cwd=root)
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("BASE = 1\n", encoding="utf-8")
    _run(["git", "add", "src/base.py"], cwd=root)
    _run(["git", "commit", "-m", "base"], cwd=root)
    return root


def _branch(repo: Path, branch: str, files: dict[str, str]) -> None:
    tree = repo.parent / f"tree-{branch.replace('/', '-')}"
    _run(["git", "worktree", "add", "-b", branch, str(tree), "master"], cwd=repo)
    for name, body in files.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        _run(["git", "add", name], cwd=tree)
    _run(["git", "commit", "-m", f"work on {branch}"], cwd=tree)
    _run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo)


CONTRACT = """\
---
feature: f
created: 2026-09-13
verdict: parallel
---

## api
- **files:** src/api.py
- **exposes:** `handler(req) -> Response`, `mnemo api --serve`
- **consumes:** `load(key)` from storage

## storage
- **files:** src/storage.py
- **exposes:** `load(key) -> Record | None`
- **consumes:** nothing
"""


@pytest.fixture
def contract(tmp_path: Path) -> Path:
    path = tmp_path / "contract.md"
    path.write_text(CONTRACT, encoding="utf-8")
    return path


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> dict:
    """``prs[branch] = (number, state)`` for ``gh pr list``; records merges."""
    prs: dict[str, tuple[int, str]] = {}
    merged: list[list[str]] = []
    real = subprocess.run

    def fake_run(args, **kw):
        if args and args[0] == "gh":
            if args[1:3] == ["pr", "list"]:
                branch = args[args.index("--head") + 1]
                if branch in prs:
                    n, state = prs[branch]
                    body = f'[{{"number":{n},"state":"{state}","url":"https://x/pull/{n}"}}]\n'
                    return subprocess.CompletedProcess(args, 0, body, "")
                return subprocess.CompletedProcess(args, 0, "[]\n", "")
            if args[1:3] == ["pr", "merge"]:
                merged.append(list(args))
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 1, "", "unexpected gh call")
        return real(args, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    prs["__merged__"] = merged  # type: ignore[assignment]
    return prs


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(land, "_repo_root", lambda: repo)
    return repo


GREEN = f"{sys.executable} -c \"import sys; sys.exit(0)\""
RED = f"{sys.executable} -c \"import sys; sys.exit(3)\""


def _args(contract, **kw) -> argparse.Namespace:
    return argparse.Namespace(
        contract=str(contract), merge=kw.pop("merge", False),
        suite=kw.pop("suite", GREEN), method=kw.pop("method", "squash"), **kw,
    )


def _delivered(repo: Path, gh: dict) -> None:
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n"})
    _branch(repo, "feat/f/api", {"src/api.py": "def handler(req):\n    pass\n"})
    gh["feat/f/storage"] = (1, "OPEN")
    gh["feat/f/api"] = (2, "OPEN")


# --- the read: prints and exits ---------------------------------------------


def test_the_view_merges_nothing(in_repo: Path, contract: Path, gh: dict, capsys) -> None:
    _delivered(in_repo, gh)

    assert land.cmd_land(_args(contract)) == 0

    assert gh["__merged__"] == []


def test_the_view_lists_pieces_in_landing_order_with_pr_and_signatures(
    in_repo: Path, contract: Path, gh: dict, capsys,
) -> None:
    _delivered(in_repo, gh)

    land.cmd_land(_args(contract))
    out = capsys.readouterr().out

    assert out.index("storage") < out.index("api")  # owner first, contract order reversed
    assert "https://x/pull/1" in out and "https://x/pull/2" in out
    assert "OPEN" in out
    assert "✓" in out            # `load` and `handler` are defined
    assert "?" in out            # `mnemo api --serve` names no identifier
    assert "✗" not in out
    assert "--merge" in out      # the next command, spelled out


def test_the_view_says_why_a_piece_cannot_land_and_exits_1(
    in_repo: Path, contract: Path, gh: dict, capsys,
) -> None:
    _branch(in_repo, "feat/f/storage", {"src/storage.py": "def save(key):\n    pass\n"})
    gh["feat/f/storage"] = (1, "OPEN")
    # api: never delivered, no PR

    assert land.cmd_land(_args(contract)) == 1
    out = capsys.readouterr().out

    assert "✗" in out
    assert "load" in out
    assert "mnemo deliver api" in out
    assert "--merge" not in out  # nothing to run next until the rows are fixed


def test_an_unusable_contract_is_refused_by_path(in_repo: Path, tmp_path: Path, capsys) -> None:
    path = tmp_path / "plan.md"
    path.write_text("# a plan, not a contract\n", encoding="utf-8")

    assert land.cmd_land(_args(path)) == 1
    assert f"contract unusable: {path}" in capsys.readouterr().out


def test_a_cycle_is_refused(in_repo: Path, tmp_path: Path, gh: dict, capsys) -> None:
    path = tmp_path / "c.md"
    path.write_text(
        "---\nfeature: f\nverdict: parallel\n---\n\n"
        "## a\n- **files:** a.py\n- **consumes:** `g()` from b\n\n"
        "## b\n- **files:** b.py\n- **consumes:** `f()` from a\n", encoding="utf-8"
    )

    assert land.cmd_land(_args(path)) == 1
    assert "cycle" in capsys.readouterr().out


def test_outside_a_repo_is_refused(monkeypatch, contract: Path, capsys) -> None:
    monkeypatch.setattr(land, "_repo_root", lambda: None)

    assert land.cmd_land(_args(contract)) == 1
    assert "git repository" in capsys.readouterr().out


# --- the merge: rehearsed first, and only then real -------------------------


def test_merge_rehearses_then_merges_every_open_pr_in_order(
    in_repo: Path, contract: Path, gh: dict, capsys,
) -> None:
    _delivered(in_repo, gh)

    assert land.cmd_land(_args(contract, merge=True)) == 0

    assert gh["__merged__"] == [
        ["gh", "pr", "merge", "https://x/pull/1", "--squash"],
        ["gh", "pr", "merge", "https://x/pull/2", "--squash"],
    ]
    out = capsys.readouterr().out
    assert "suite green" in out


def test_a_red_suite_merges_nothing(in_repo: Path, contract: Path, gh: dict, capsys) -> None:
    _delivered(in_repo, gh)

    assert land.cmd_land(_args(contract, merge=True, suite=RED)) == 1

    assert gh["__merged__"] == []
    out = capsys.readouterr().out
    assert "storage" in out and "suite red" in out


def test_merge_refuses_a_contract_the_view_would_refuse(
    in_repo: Path, contract: Path, gh: dict, capsys,
) -> None:
    _branch(in_repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n"})
    gh["feat/f/storage"] = (1, "OPEN")  # api never delivered

    assert land.cmd_land(_args(contract, merge=True)) == 1

    assert gh["__merged__"] == []
    assert "mnemo deliver api" in capsys.readouterr().out


def test_merge_takes_the_method(in_repo: Path, contract: Path, gh: dict) -> None:
    _delivered(in_repo, gh)

    land.cmd_land(_args(contract, merge=True, method="rebase"))

    assert all(call[-1] == "--rebase" for call in gh["__merged__"])


def test_merge_survives_a_gh_refusal_and_says_where_it_stopped(
    in_repo: Path, contract: Path, gh: dict, capsys, monkeypatch,
) -> None:
    _delivered(in_repo, gh)
    monkeypatch.setattr(
        landing, "merge_prs",
        lambda states, **kw: [landing.Step("storage", ok=False, detail="not mergeable")],
    )

    assert land.cmd_land(_args(contract, merge=True)) == 1

    out = capsys.readouterr().out
    assert "storage" in out and "not mergeable" in out
    assert "mnemo land" in out  # the rerun hint: what landed is skipped next time


# --- the parser -------------------------------------------------------------


def test_land_is_wired_with_its_flags() -> None:
    from mnemo.cli.parser import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["land", "docs/c.md", "--merge", "--suite", "make test",
                              "--method", "merge"])

    assert args.command == "land"
    assert args.contract == "docs/c.md"
    assert args.merge is True
    assert args.suite == "make test"
    assert args.method == "merge"


def test_land_defaults_to_the_read_only_view() -> None:
    from mnemo.cli.parser import _build_parser

    args = _build_parser().parse_args(["land", "docs/c.md"])

    assert args.merge is False
    assert args.method == "squash"


# --- --suite splitting on Windows (#236, windows CI) --------------------------


def test_split_suite_keeps_windows_paths_and_unquotes_code():
    """POSIX splitting ate the backslashes of an unquoted interpreter path
    (`WinError 2` on the windows job); non-POSIX splitting keeps them but
    also keeps the quotes, which would hand `-c` a string literal."""
    cmd = r'C:\hostedtoolcache\windows\Python\3.11.9\x64\python.exe -c "import sys; sys.exit(0)"'
    assert land.split_suite(cmd, windows=True) == [
        r"C:\hostedtoolcache\windows\Python\3.11.9\x64\python.exe",
        "-c",
        "import sys; sys.exit(0)",
    ]
    # The default, which `shlex.quote` wraps in single quotes on every OS.
    quoted = r"'C:\Python\python.exe' -m pytest -q"
    assert land.split_suite(quoted, windows=True) == [r"C:\Python\python.exe", "-m", "pytest", "-q"]
    # POSIX is untouched: same input, the shell's rules.
    assert land.split_suite("python -c 'import sys; sys.exit(0)'", windows=False) == [
        "python", "-c", "import sys; sys.exit(0)",
    ]


def test_split_suite_posix_reproduces_the_windows_failure():
    """The pre-fix behaviour, pinned so the reason for the branch stays visible."""
    cmd = r"C:\hostedtoolcache\python.exe -c x"
    assert land.split_suite(cmd, windows=False)[0] == "C:hostedtoolcachepython.exe"
