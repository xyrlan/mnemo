"""The last metre of a contract: the order, the signatures, the rehearsal.

Every git fact is asserted against **real repositories**, as
:mod:`tests.unit.test_delivery` does and for the same reason: the claim of
:mod:`mnemo.core.landing` is that git is the authority on whether a signature
exists on a branch and whether two pieces merge, and a stubbed git tests the
stub. ``gh`` is never invoked — it is stubbed at the subprocess boundary — and
the suite a rehearsal runs is a tiny interpreter command, so a red suite is a
real non-zero exit rather than a mocked one.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from mnemo.core import contracts, landing
from mnemo.core.contracts import Contract, Piece


def _run(args, *, cwd) -> None:
    subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo with one commit on ``master`` and no remote."""
    root = tmp_path / "mnemo"
    root.mkdir()
    _run(["git", "init", "-b", "master"], cwd=root)
    _run(["git", "config", "user.email", "t@example.com"], cwd=root)
    _run(["git", "config", "user.name", "t"], cwd=root)
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("BASE = 1\n")
    _run(["git", "add", "src/base.py"], cwd=root)
    _run(["git", "commit", "-m", "base"], cwd=root)
    return root


def _branch(repo: Path, branch: str, files: dict[str, str]) -> None:
    """Commit *files* on *branch* (from master) in a throwaway worktree."""
    tree = repo.parent / f"tree-{branch.replace('/', '-')}"
    _run(["git", "worktree", "add", "-b", branch, str(tree), "master"], cwd=repo)
    for name, body in files.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        _run(["git", "add", name], cwd=tree)
    _run(["git", "commit", "-m", f"work on {branch}"], cwd=tree)
    _run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo)


@pytest.fixture(autouse=True)
def no_gh(monkeypatch: pytest.MonkeyPatch):
    """No test here reaches the network: ``gh`` answers "no PR"."""
    real = subprocess.run

    def fake_run(args, **kw):
        if args and args[0] == "gh":
            return subprocess.CompletedProcess(args, 0, "[]", "")
        return real(args, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)


def _contract(*pieces: Piece, feature: str = "f") -> Contract:
    return Contract(feature=feature, verdict="parallel", pieces=list(pieces))


# --- order: owners before consumers, contract order otherwise ---------------


def test_order_lands_an_owner_before_its_consumer() -> None:
    api = Piece("api", files=["api.py"], consumes=[("`load(k)`", "storage")])
    storage = Piece("storage", files=["s.py"], exposes=["`load(k)`"])

    assert [p.slug for p in landing.order(_contract(api, storage))] == [
        "storage", "api",
    ]


def test_order_keeps_contract_order_between_independent_pieces() -> None:
    a = Piece("a", files=["a.py"])
    b = Piece("b", files=["b.py"])
    c = Piece("c", files=["c.py"])

    assert [p.slug for p in landing.order(_contract(c, a, b))] == ["c", "a", "b"]


def test_order_refuses_a_cycle_by_name() -> None:
    """``_validate`` admits A<->B; landing has no order for it and says so."""
    a = Piece("a", files=["a.py"], consumes=[("`g()`", "b")])
    b = Piece("b", files=["b.py"], consumes=[("`f()`", "a")])

    with pytest.raises(landing.LandingError) as exc:
        landing.order(_contract(a, b))
    assert "a" in str(exc.value) and "b" in str(exc.value)


# --- the name a signature is looked up by ----------------------------------


@pytest.mark.parametrize(
    "signature, name",
    [
        ("`load(key) -> Record | None`", "load"),
        ("`ready(worktree, *, repo_root) -> Readiness`", "ready"),
        ("`Readiness.ready`", "ready"),
        ("`Readiness.ready -> bool`", "ready"),
        ("`class Piece`", "Piece"),
        ("`def parse(x)`", "parse"),
        ("`async def fetch(x)`", "fetch"),
        ("`BASE: str`", "BASE"),
        ("`parse_contract`", "parse_contract"),
        ("`mnemo dispatch --contract <path>`", None),
        ("`--json`", None),
        ("nothing", None),
    ],
)
def test_signature_name(signature: str, name) -> None:
    assert landing.signature_name(signature) == name


# --- presence: a definition in the piece's files at a ref -------------------


def test_a_def_in_the_boundary_is_present(repo: Path) -> None:
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    return None\n"})
    piece = Piece("storage", files=["src/storage.py"], exposes=["`load(key) -> R`"])

    assert landing.present("`load(key) -> R`", piece=piece, ref="feat/f/storage",
                           repo_root=repo) is True


def test_a_name_defined_nowhere_in_the_boundary_is_missing(repo: Path) -> None:
    _branch(repo, "feat/f/storage", {"src/storage.py": "def save(key):\n    pass\n"})
    piece = Piece("storage", files=["src/storage.py"], exposes=["`load(key)`"])

    assert landing.present("`load(key)`", piece=piece, ref="feat/f/storage",
                           repo_root=repo) is False


def test_a_name_defined_outside_the_boundary_does_not_count(repo: Path) -> None:
    """The contract's promise is *this piece* delivers it, in *these* files."""
    _branch(repo, "feat/f/storage", {
        "src/storage.py": "x = 1\n",
        "src/elsewhere.py": "def load(key):\n    pass\n",
    })
    piece = Piece("storage", files=["src/storage.py"], exposes=["`load(key)`"])

    assert landing.present("`load(key)`", piece=piece, ref="feat/f/storage",
                           repo_root=repo) is False


def test_a_glob_boundary_is_expanded_against_the_ref(repo: Path) -> None:
    _branch(repo, "feat/f/storage", {"src/pkg/deep.py": "class Store:\n    pass\n"})
    piece = Piece("storage", files=["src/pkg/*.py"], exposes=["`class Store`"])

    assert landing.present("`class Store`", piece=piece, ref="feat/f/storage",
                           repo_root=repo) is True


def test_a_method_counts_when_defined_indented(repo: Path) -> None:
    _branch(repo, "feat/f/d", {
        "src/d.py": "class Readiness:\n    @property\n    def ready(self):\n        return True\n",
    })
    piece = Piece("d", files=["src/d.py"], exposes=["`Readiness.ready`"])

    assert landing.present("`Readiness.ready`", piece=piece, ref="feat/f/d",
                           repo_root=repo) is True


def test_a_signature_without_an_identifier_is_unverifiable(repo: Path) -> None:
    piece = Piece("cli", files=["src/base.py"], exposes=["`mnemo land <path>`"])

    assert landing.present("`mnemo land <path>`", piece=piece, ref="master",
                           repo_root=repo) is None


def test_a_missing_file_in_the_boundary_is_not_an_error(repo: Path) -> None:
    """A piece that never created one of its files is missing, not crashed."""
    piece = Piece("s", files=["src/never.py", "src/base.py"], exposes=["`BASE`"])

    assert landing.present("`BASE`", piece=piece, ref="master", repo_root=repo) is True


# --- inspect: the read-only view of a delivered contract -------------------


@pytest.fixture
def gh_prs(monkeypatch: pytest.MonkeyPatch) -> dict:
    """``branch -> (number, state)`` answered by a stubbed ``gh pr list``.

    The stdout shape is the one ``gh`` really emits (see
    ``tests.unit.test_delivery.REAL_GH_MERGED``); only the values vary.
    """
    prs: dict[str, tuple[int, str]] = {}
    real = subprocess.run

    def fake_run(args, **kw):
        if args and args[0] == "gh":
            if args[1:3] == ["pr", "list"]:
                branch = args[args.index("--head") + 1]
                if branch in prs:
                    number, state = prs[branch]
                    body = (f'[{{"number":{number},"state":"{state}",'
                            f'"url":"https://x/pull/{number}"}}]\n')
                    return subprocess.CompletedProcess(args, 0, body, "")
                return subprocess.CompletedProcess(args, 0, "[]\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")
        return real(args, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return prs


STORAGE = Piece("storage", files=["src/storage.py"], exposes=["`load(key) -> R`"])
API = Piece("api", files=["src/api.py"], exposes=["`handler(req)`"],
            consumes=[("`load(key)`", "storage")])


def _two_pieces(repo: Path) -> Contract:
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n"})
    _branch(repo, "feat/f/api", {"src/api.py": "def handler(req):\n    pass\n"})
    return _contract(API, STORAGE)


def test_inspect_lists_pieces_in_landing_order_with_pr_ref_and_signatures(
    repo: Path, gh_prs: dict,
) -> None:
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")

    states = landing.inspect(_two_pieces(repo), repo_root=repo)

    assert [s.piece.slug for s in states] == ["storage", "api"]
    storage, api = states
    assert storage.branch == "feat/f/storage"
    assert storage.ref == "feat/f/storage"
    assert storage.pr == "https://x/pull/1" and storage.pr_state == "OPEN"
    assert storage.exposes == [("`load(key) -> R`", True)]
    assert api.consumes == [("`load(key)`", "storage", True)]
    assert all(s.landable for s in states)


def test_inspect_refuses_a_piece_without_a_pr_naming_deliver(repo: Path, gh_prs) -> None:
    gh_prs["feat/f/storage"] = (1, "OPEN")

    storage, api = landing.inspect(_two_pieces(repo), repo_root=repo)

    assert storage.landable
    assert not api.landable
    assert "deliver" in api.reason and "api" in api.reason


def test_inspect_refuses_a_piece_whose_branch_is_gone(repo: Path, gh_prs) -> None:
    """An open PR whose branch nothing here can reach cannot be rehearsed."""
    gh_prs["feat/f/storage"] = (1, "OPEN")
    contract = _contract(STORAGE)  # never branched

    (state,) = landing.inspect(contract, repo_root=repo)

    assert state.ref is None
    assert not state.landable
    assert "feat/f/storage" in state.reason


def test_inspect_checks_a_merged_piece_on_the_base(repo: Path, gh_prs) -> None:
    """Merged and branch deleted is the normal end state: its work is in master."""
    gh_prs["feat/f/storage"] = (1, "MERGED")
    (repo / "src" / "storage.py").write_text("def load(key):\n    pass\n")
    _run(["git", "add", "src/storage.py"], cwd=repo)
    _run(["git", "commit", "-m", "storage landed"], cwd=repo)

    (state,) = landing.inspect(_contract(STORAGE), repo_root=repo)

    assert state.merged
    assert state.ref == "master"
    assert state.exposes == [("`load(key) -> R`", True)]
    assert state.landable


def test_inspect_refuses_a_missing_exposed_signature(repo: Path, gh_prs) -> None:
    gh_prs["feat/f/storage"] = (1, "OPEN")
    _branch(repo, "feat/f/storage", {"src/storage.py": "def save(key):\n    pass\n"})

    (state,) = landing.inspect(_contract(STORAGE), repo_root=repo)

    assert state.exposes == [("`load(key) -> R`", False)]
    assert not state.landable
    assert "load" in state.reason


def test_inspect_flags_a_consumed_name_the_owner_never_exposes(repo: Path, gh_prs) -> None:
    """The static half of the signature check: the contract disagrees with itself."""
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")
    api = Piece("api", files=["src/api.py"], consumes=[("`fetch(key)`", "storage")])
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n"})
    _branch(repo, "feat/f/api", {"src/api.py": "x = 1\n"})

    storage, api_state = landing.inspect(_contract(api, STORAGE), repo_root=repo)

    assert api_state.consumes == [("`fetch(key)`", "storage", False)]
    assert not api_state.landable
    assert "fetch" in api_state.reason and "storage" in api_state.reason


def test_inspect_reports_an_unverifiable_signature_as_neither(repo: Path, gh_prs) -> None:
    gh_prs["feat/f/cli"] = (1, "OPEN")
    cli = Piece("cli", files=["src/cli.py"], exposes=["`mnemo land <path>`"])
    _branch(repo, "feat/f/cli", {"src/cli.py": "x = 1\n"})

    (state,) = landing.inspect(_contract(cli), repo_root=repo)

    assert state.exposes == [("`mnemo land <path>`", None)]
    assert state.landable


def test_inspect_raises_on_a_cycle(repo: Path, gh_prs) -> None:
    a = Piece("a", files=["a.py"], consumes=[("`g()`", "b")])
    b = Piece("b", files=["b.py"], consumes=[("`f()`", "a")])

    with pytest.raises(landing.LandingError):
        landing.inspect(_contract(a, b), repo_root=repo)


# --- rehearse: merge in order, check, run the suite, touch nothing ----------

# A "suite" that is green unless the merged tree contains `src/red`, and
# records every run. Real exit codes, real cwd — the rehearsal's claim is
# that the suite ran *in the merged tree*, and a stub could not show that.
def _suite(log: Path) -> list[str]:
    return [
        sys.executable, "-c",
        "import pathlib, sys; "
        f"pathlib.Path({str(log)!r}).open('a').write(pathlib.Path().resolve().name + chr(10)); "
        "sys.exit(1 if pathlib.Path('src/red').exists() else 0)",
    ]


def _worktrees(repo: Path) -> list[str]:
    out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo,
                         capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.startswith("worktree ")]


def test_rehearse_merges_each_piece_in_order_and_runs_the_suite_each_time(
    repo: Path, gh_prs: dict, tmp_path: Path,
) -> None:
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")
    states = landing.inspect(_two_pieces(repo), repo_root=repo)
    log = tmp_path / "runs.log"
    before = _worktrees(repo)

    result = landing.rehearse(states, repo_root=repo, suite=_suite(log))

    assert result.ok, result.steps
    assert [s.slug for s in result.steps] == ["storage", "api"]
    runs = log.read_text().splitlines()
    assert len(runs) == 2
    assert all(name.startswith("mnemo-land-") for name in runs)
    assert _worktrees(repo) == before  # the rehearsal tree is gone
    # And master is untouched: nothing was merged for real.
    assert not (repo / "src" / "storage.py").exists()


def test_rehearse_stops_at_the_first_conflict(repo: Path, gh_prs, tmp_path) -> None:
    gh_prs["feat/f/a"] = (1, "OPEN")
    gh_prs["feat/f/b"] = (2, "OPEN")
    a = Piece("a", files=["src/base.py"])
    b = Piece("b", files=["src/base.py"])
    _branch(repo, "feat/f/a", {"src/base.py": "BASE = 2\n"})
    _branch(repo, "feat/f/b", {"src/base.py": "BASE = 3\n"})
    states = landing.inspect(_contract(a, b), repo_root=repo)
    log = tmp_path / "runs.log"
    before = _worktrees(repo)

    result = landing.rehearse(states, repo_root=repo, suite=_suite(log))

    assert not result.ok
    assert result.failed.slug == "b"
    assert "conflict" in result.failed.detail.lower()
    assert len(log.read_text().splitlines()) == 1  # a's suite ran; b never merged
    assert _worktrees(repo) == before


def test_rehearse_stops_when_a_consumed_name_is_absent_from_the_merged_tree(
    repo: Path, gh_prs, tmp_path,
) -> None:
    """The check the issue asks for, at the step where it can first be made.

    The contract says storage exposes ``load``; the branch defines ``save``.
    ``inspect`` already flags storage's own row; this is the *consumer's*
    step, and it must not pass on the strength of the contract's text.
    """
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")
    _branch(repo, "feat/f/storage", {"src/storage.py": "def save(key):\n    pass\n"})
    _branch(repo, "feat/f/api", {"src/api.py": "def handler(req):\n    pass\n"})
    states = landing.inspect(_contract(API, STORAGE), repo_root=repo)

    # `inspect` already refuses storage's row; `force` is how the rehearsal's
    # own check is reached, and it is the check under test here.
    result = landing.rehearse(states, repo_root=repo, suite=_suite(tmp_path / "l"),
                              force=True)

    assert not result.ok
    assert result.failed.slug == "storage"  # its own exposes check fails first
    assert "load" in result.failed.detail


def test_rehearse_checks_the_consumer_against_the_owner_in_the_merged_tree(
    repo: Path, gh_prs, tmp_path,
) -> None:
    """Owner's row passes (it exposes what it says); the consumer names more."""
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")
    api = Piece("api", files=["src/api.py"],
                consumes=[("`load(key)`", "storage"), ("`purge()`", "storage")])
    storage = Piece("storage", files=["src/storage.py"],
                    exposes=["`load(key)`", "`purge()`"])
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n"})
    _branch(repo, "feat/f/api", {"src/api.py": "x = 1\n"})
    states = landing.inspect(_contract(api, storage), repo_root=repo)
    # Force the rehearsal past inspect's own refusal: this asserts the
    # rehearsal's check, not inspect's.
    result = landing.rehearse(states, repo_root=repo, suite=_suite(tmp_path / "l"),
                              force=True)

    assert not result.ok
    assert result.failed.slug == "storage"
    assert "purge" in result.failed.detail


def test_rehearse_stops_at_a_red_suite_with_its_output(repo: Path, gh_prs, tmp_path) -> None:
    gh_prs["feat/f/storage"] = (1, "OPEN")
    gh_prs["feat/f/api"] = (2, "OPEN")
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n",
                                     "src/red": ""})
    _branch(repo, "feat/f/api", {"src/api.py": "def handler(req):\n    pass\n"})
    states = landing.inspect(_contract(API, STORAGE), repo_root=repo)
    log = tmp_path / "runs.log"

    result = landing.rehearse(states, repo_root=repo, suite=_suite(log))

    assert not result.ok
    assert result.failed.slug == "storage"
    assert "suite" in result.failed.detail
    assert len(log.read_text().splitlines()) == 1  # api never ran


def test_rehearse_skips_a_merged_piece(repo: Path, gh_prs, tmp_path) -> None:
    gh_prs["feat/f/storage"] = (1, "MERGED")
    gh_prs["feat/f/api"] = (2, "OPEN")
    (repo / "src" / "storage.py").write_text("def load(key):\n    pass\n")
    _run(["git", "add", "src/storage.py"], cwd=repo)
    _run(["git", "commit", "-m", "storage landed"], cwd=repo)
    _branch(repo, "feat/f/api", {"src/api.py": "def handler(req):\n    pass\n"})
    states = landing.inspect(_contract(API, STORAGE), repo_root=repo)
    log = tmp_path / "runs.log"

    result = landing.rehearse(states, repo_root=repo, suite=_suite(log))

    assert result.ok
    assert [(s.slug, s.skipped) for s in result.steps] == [("storage", True), ("api", False)]
    assert len(log.read_text().splitlines()) == 1


def test_rehearse_refuses_states_that_are_not_landable(repo: Path, gh_prs, tmp_path) -> None:
    """A rehearsal of a contract inspect refused would merge a piece with no PR."""
    gh_prs["feat/f/storage"] = (1, "OPEN")
    states = landing.inspect(_two_pieces(repo), repo_root=repo)  # api has no PR

    with pytest.raises(landing.LandingError) as exc:
        landing.rehearse(states, repo_root=repo, suite=_suite(tmp_path / "l"))
    assert "api" in str(exc.value)


def test_rehearse_prepends_src_to_pythonpath_when_the_tree_has_one(
    repo: Path, gh_prs, tmp_path, monkeypatch,
) -> None:
    """An editable install resolves to the checkout it came from, never the
    rehearsal tree; a ``src`` layout gets its own tree first on the path."""
    gh_prs["feat/f/storage"] = (1, "OPEN")
    _branch(repo, "feat/f/storage", {"src/storage.py": "def load(key):\n    pass\n"})
    states = landing.inspect(_contract(STORAGE), repo_root=repo)
    seen = tmp_path / "path.txt"
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    suite = [sys.executable, "-c",
             f"import os, pathlib; pathlib.Path({str(seen)!r}).write_text(os.environ['PYTHONPATH'])"]

    assert landing.rehearse(states, repo_root=repo, suite=suite).ok

    parts = seen.read_text().split(os.pathsep)
    first = Path(parts[0])
    assert first.name == "src" and first.parent.name.startswith("mnemo-land-")
    assert parts[1] == "/elsewhere"


# --- merge: gh, in order, stopping at the first refusal --------------------


def _gh_merge(monkeypatch: pytest.MonkeyPatch, *, fail_on: str = "") -> list:
    calls: list[list[str]] = []
    real = subprocess.run

    def fake_run(args, **kw):
        if args and args[0] == "gh":
            calls.append(list(args))
            if fail_on and fail_on in args:
                return subprocess.CompletedProcess(
                    args, 1, "", "GraphQL: Pull request is not mergeable")
            return subprocess.CompletedProcess(args, 0, "", "")
        return real(args, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _state(slug: str, pr: str | None, pr_state: str | None = "OPEN") -> landing.PieceState:
    return landing.PieceState(
        piece=Piece(slug, files=["x.py"]), branch=f"feat/f/{slug}", ref=f"feat/f/{slug}",
        pr=pr, pr_state=pr_state, exposes=[], consumes=[],
    )


def test_merge_prs_merges_in_order_and_skips_the_already_merged(monkeypatch) -> None:
    calls = _gh_merge(monkeypatch)
    states = [_state("storage", "https://x/pull/1", "MERGED"), _state("api", "https://x/pull/2")]

    steps = landing.merge_prs(states, repo_root="/repo")

    assert [(s.slug, s.ok, s.skipped) for s in steps] == [("storage", True, True), ("api", True, False)]
    assert calls == [["gh", "pr", "merge", "https://x/pull/2", "--squash"]]


def test_merge_prs_stops_at_the_first_refusal_with_gh_stderr(monkeypatch) -> None:
    calls = _gh_merge(monkeypatch, fail_on="https://x/pull/1")
    states = [_state("storage", "https://x/pull/1"), _state("api", "https://x/pull/2")]

    steps = landing.merge_prs(states, repo_root="/repo")

    assert [s.slug for s in steps] == ["storage"]
    assert not steps[0].ok and "not mergeable" in steps[0].detail
    assert len(calls) == 1  # api was never attempted


def test_merge_prs_takes_the_method(monkeypatch) -> None:
    calls = _gh_merge(monkeypatch)

    landing.merge_prs([_state("a", "https://x/pull/1")], repo_root="/repo", method="rebase")

    assert calls[0][-1] == "--rebase"
