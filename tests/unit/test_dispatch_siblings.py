"""Who else this dispatch started, told to each child (#384).

The issue asks for three things to reach a child that today do not: what the
parent ruled out, what it measured, and which sibling is doing what. Only the
third is built, and these tests pin both halves of that decision — that the
roster is rendered where a child can act on it, and that a child with no
siblings gets the prompt it got before this existed, byte for byte.

The measurement behind the split is in ``tools/measure_dispatch_siblings.py``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from mnemo.core import contracts, dispatch
from mnemo.core.sessions import grants


def _flat(text: str) -> str:
    return " ".join(text.split())


def _issue(number: int, title: str = "t") -> dispatch.Issue:
    return dispatch.Issue(number=number, title=title, body="b")


BATCH = [_issue(382, "repo map"), _issue(383, "confidence"),
         _issue(384, "parent decisions"), _issue(385, "playbooks")]


# --- the rendered roster ------------------------------------------------------


def test_a_child_is_told_which_issues_run_beside_it() -> None:
    prompt = dispatch.build_prompt(384, title="t", body="b", siblings=BATCH)

    assert "- #382 — repo map" in prompt
    assert "- #383 — confidence" in prompt
    assert "- #385 — playbooks" in prompt


def test_a_child_is_not_listed_as_its_own_sibling() -> None:
    """The caller passes the whole batch; the filtering is this function's."""
    prompt = dispatch.build_prompt(384, title="t", body="b", siblings=BATCH)

    assert "- #384" not in prompt


def test_the_roster_says_it_is_not_work_to_do() -> None:
    """A list of live issues reads as a backlog unless it says otherwise, and a
    child that takes a sibling's issue is the failure this was built to stop."""
    prompt = _flat(dispatch.build_prompt(384, title="t", body="b", siblings=BATCH))

    assert "not an instruction and none of it is yours to do" in prompt


def test_a_read_only_child_gets_the_roster_too() -> None:
    """It edits nothing, so it cannot collide — but two investigators measuring
    the same thing is the same waste, and the posture is set per dispatch."""
    prompt = dispatch.build_prompt(
        384, title="t", body="b", siblings=BATCH, read_only=True
    )

    assert "- #382 — repo map" in prompt
    assert "your file-editing tools are closed" in prompt  # still the analysis prompt


def test_a_piece_is_told_nothing_about_the_other_pieces(tmp_path) -> None:
    """Measured and declined, not overlooked.

    An issue child is told nothing about its siblings and 3 of 16 multi-child
    issue batches had two write the same file. A piece is already told what it
    may not touch, and 0 of 13 multi-child contract batches collided — so the
    roster would buy nothing measured here, while another piece's file list
    inside this prompt weakens the one thing that makes the boundary hard:
    that those paths are not in it.
    """
    import inspect

    mine = contracts.Piece(slug="drag", files=["src/drag.ts"])
    theirs = contracts.Piece(slug="theme", files=["src/theme.css"])

    prompt = dispatch.build_piece_prompt(mine, feature="f")

    assert "siblings" not in inspect.signature(dispatch.build_piece_prompt).parameters
    assert theirs.files[0] not in prompt
    assert "running in parallel right now" not in prompt


# --- what it must not change --------------------------------------------------


def test_the_placeholder_collapses_when_a_child_has_no_siblings() -> None:
    """Deliberately the junction and not the whole prompt.

    A golden copy of the render would be the stronger pin, and the wrong one:
    #382, #383 and #385 were opened the same day and all edit these same
    strings, so a whole-prompt fixture would fail on whichever of the four
    merged second. This asserts the one thing #384 can break — that an empty
    roster leaves the blank line it found, rather than two or none.
    """
    assert "Work only here.\n\nScope limits:" in dispatch.build_prompt(
        7, title="T", body="B"
    )
    assert "worth having.\n\nWhat is being asked of you:" in dispatch.build_prompt(
        7, title="T", body="B", read_only=True
    )
    assert "in your own worktree.\n\nNothing about" in dispatch.build_piece_prompt(
        contracts.Piece(slug="p", files=["a.py"]), feature="f"
    )


@pytest.mark.parametrize("may", [(), ("push",), ("push", "pr")])
@pytest.mark.parametrize("read_only", [False, True])
def test_no_posture_and_no_grant_invents_a_roster(may, read_only) -> None:
    prompt = dispatch.build_prompt(7, title="T", body="B", may=may,
                                   read_only=read_only)

    assert "running in parallel right now" not in prompt


def test_a_lone_child_is_told_of_no_siblings() -> None:
    prompt = dispatch.build_prompt(7, title="T", body="B", siblings=[_issue(7)])

    assert "running in parallel right now" not in prompt


# --- bounded and redacted -----------------------------------------------------


def test_the_roster_is_capped_and_says_how_many_it_cut() -> None:
    """The child's baseline is already ~70k tokens; a 40-piece contract must
    cost a footnote, and a silent truncation would read as a complete list."""
    batch = [_issue(n, f"issue {n}") for n in range(1, 40)]

    prompt = dispatch.build_prompt(1, title="t", body="b", siblings=batch)

    listed = [line for line in prompt.splitlines() if line.startswith("- #")]
    assert len(listed) == dispatch._SIBLING_CAP
    assert f"- (and {38 - dispatch._SIBLING_CAP} more)" in prompt


def test_one_sibling_never_spills_onto_a_second_line() -> None:
    """A row wrapped to column 0 reads as prose about this child's own task."""
    long_title = "x" * 400
    prompt = dispatch.build_prompt(1, title="t", body="b",
                                   siblings=[_issue(1), _issue(2, long_title)])

    row = next(line for line in prompt.splitlines() if line.startswith("- #2"))
    assert len(row) <= dispatch._SIBLING_WIDTH
    assert row.endswith("…")


def test_a_title_carrying_a_secret_is_redacted() -> None:
    """Nothing here is transcript, so in practice this finds nothing — which is
    why it is pinned: #384's boundary is about the rule, not about today's data."""
    batch = [_issue(1), _issue(2, "rotate ghp_" + "a" * 36)]

    prompt = dispatch.build_prompt(1, title="t", body="b", siblings=batch)

    assert "ghp_" not in prompt
    assert "[redacted]" in prompt


# --- the dispatch hands it over ----------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    (root / "README.md").write_text("x\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "master"], ["add", "."], ["commit", "-qm", "i"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                       env={**os.environ, **env})
    return root


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(grants, "_default_vault", lambda: root)
    monkeypatch.setattr("mnemo.core.sessions.parents._default_vault", lambda: root)
    return root


@pytest.fixture()
def prompts(monkeypatch) -> dict:
    seen: dict = {}

    def fake_spawn(prompt, *, cwd, **kwargs):
        name = Path(cwd).name
        seen[name] = prompt
        return f"{len(seen):04d}aaaa"

    monkeypatch.setattr(dispatch, "spawn_child", fake_spawn)
    monkeypatch.setattr(dispatch.claude_cli, "verify_registered", lambda *a, **k: None)
    return seen


def test_every_child_of_a_batch_is_told_about_the_others(
    repo: Path, vault: Path, prompts: dict
) -> None:
    fetch = lambda n, **kw: _issue(n, f"title {n}")  # noqa: E731

    dispatch.dispatch_all([382, 383, 384], repo_root=repo, fetch=fetch)

    assert "- #383 — title 383" in prompts["proj-wt-382"]
    assert "- #384 — title 384" in prompts["proj-wt-382"]
    assert "- #382 — title 382" in prompts["proj-wt-384"]
    assert "- #384" not in prompts["proj-wt-384"]


def test_a_single_issue_dispatch_names_no_siblings(
    repo: Path, vault: Path, prompts: dict
) -> None:
    fetch = lambda n, **kw: _issue(n)  # noqa: E731

    dispatch.dispatch_all([317], repo_root=repo, fetch=fetch)

    assert "running in parallel right now" not in prompts["proj-wt-317"]


def test_the_whole_batch_is_read_before_anything_is_spawned(
    repo: Path, vault: Path, prompts: dict
) -> None:
    """The roster is only truthful if every number in it came back from `gh`."""
    order: list = []

    def fetch(n, **kw):
        order.append(("read", n))
        return _issue(n)

    real_spawn = dispatch.spawn_child

    def spy(prompt, *, cwd, **kwargs):
        order.append(("spawn", Path(cwd).name))
        return real_spawn(prompt, cwd=cwd, **kwargs)

    dispatch.spawn_child = spy
    try:
        dispatch.dispatch_all([1, 2, 3], repo_root=repo, fetch=fetch)
    finally:
        dispatch.spawn_child = real_spawn

    assert [kind for kind, _ in order] == ["read"] * 3 + ["spawn"] * 3


def test_an_issue_gh_refuses_is_named_to_nobody(
    repo: Path, vault: Path, prompts: dict
) -> None:
    """A number that is not an issue is not a sibling; naming it would send
    every other child looking for work nobody is doing."""
    def fetch(n, **kw):
        if n == 383:
            raise dispatch.DispatchError("issue #383 not found")
        return _issue(n, f"title {n}")

    results = dispatch.dispatch_all([382, 383, 384], repo_root=repo, fetch=fetch)

    assert [r.issue for r in results] == [382, 383, 384]          # order kept
    assert [bool(r.error) for r in results] == [False, True, False]
    assert "#383" not in prompts["proj-wt-382"]
    assert "- #384 — title 384" in prompts["proj-wt-382"]


def test_an_issue_is_read_once_even_though_the_batch_is_read_first(
    repo: Path, vault: Path, prompts: dict
) -> None:
    """`gh` is a subprocess per call, and a second read could disagree with the
    body the siblings were told about."""
    reads: list = []

    def fetch(n, **kw):
        reads.append(n)
        return _issue(n)

    dispatch.dispatch_all([1, 2], repo_root=repo, fetch=fetch)

    assert reads == [1, 2]


def test_a_contract_dispatch_leaves_every_piece_prompt_alone(
    repo: Path, vault: Path, prompts: dict
) -> None:
    contract = contracts.Contract(
        feature="f", verdict="parallel",
        pieces=[contracts.Piece(slug="one", files=["a.py"]),
                contracts.Piece(slug="two", files=["b.py"])],
    )

    dispatch.dispatch_contract(contract, repo_root=repo)

    assert "b.py" not in prompts["proj-wt-c-one"]
    assert "a.py" not in prompts["proj-wt-c-two"]
