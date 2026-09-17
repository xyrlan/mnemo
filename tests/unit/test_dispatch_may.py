"""What a dispatched child may publish without asking again (#317).

A child that finished stopped at "may I push / open a PR?", and the answer the
maintainer sent through the desktop arrived as a peer message that cannot carry
approval (#309). The dispatch prompt is the one message a child reads as the
maintainer's own, so the permission is rendered there — and these tests pin
both halves of that: what each grant tells the child, and that no grant tells
it exactly what it was told before.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from mnemo.core import contracts, dispatch
from mnemo.core.sessions import grants
from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.render import render_queue

def _flat(text: str) -> str:
    """The prompt with its wrapping undone, so a phrase can be asserted whole."""
    return " ".join(text.split())


# --- the vocabulary ---------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [
    ("push", ("push",)),
    ("pr", ("push", "pr")),         # a PR is opened from a pushed branch
    ("push,pr", ("push", "pr")),
    ("pr, push", ("push", "pr")),   # recorded in one order whatever was typed
    (" PR ", ("push", "pr")),
    ("none", ()),
    ("", ()),
    (None, ()),
])
def test_a_grant_parses_to_its_canonical_form(value, expected) -> None:
    assert grants.parse(value) == expected


def test_merge_is_refused_by_name() -> None:
    """Not merely unknown: landing is `mnemo land`'s, and the refusal says so."""
    with pytest.raises(grants.GrantError, match="never merges"):
        grants.parse("push,merge")


@pytest.mark.parametrize("value", ["publish", "force-push", "tag", "release"])
def test_anything_outside_the_vocabulary_is_refused(value: str) -> None:
    with pytest.raises(grants.GrantError, match="push, pr"):
        grants.parse(value)


# --- the rendered prompt, per value ------------------------------------------


def _issue_prompt(may=()) -> str:
    return dispatch.build_prompt(317, title="t", body="b", may=may)


def test_no_grant_renders_the_scope_limit_it_always_rendered() -> None:
    prompt = _issue_prompt()
    assert "Scope limits:\n- Do not merge or push without asking.\n- Do not touch" in prompt
    assert "granted" not in prompt


def test_push_grants_the_push_and_withholds_the_pr() -> None:
    prompt = _flat(_issue_prompt(grants.parse("push")))

    assert "push `fix/issue-317` to origin, without asking first" in prompt
    assert "the maintainer granted this when they dispatched you" in prompt
    assert "do not ask for it again" in prompt
    assert "Do not open a pull request: `mnemo deliver` does that." in prompt
    assert "Closes #317" not in prompt
    assert "without asking." not in prompt  # the old limit is gone, not contradicted


def test_pr_grants_the_push_and_the_pr_with_its_closing_trailer() -> None:
    prompt = _flat(_issue_prompt(grants.parse("pr")))

    assert "push `fix/issue-317` to origin and open a pull request for it" in prompt
    assert "ready for review rather than a draft" in prompt
    assert "End the pull request body with `Closes #317`." in prompt
    assert "do not ask for it again" in prompt
    assert "Do not open a pull request" not in prompt


@pytest.mark.parametrize("value", ["push", "pr"])
def test_every_grant_keeps_the_limits_that_are_not_publishing(value: str) -> None:
    prompt = _flat(_issue_prompt(grants.parse(value)))

    assert prompt.count("Do not merge.") == 1
    assert "Once the full test suite passes" in prompt
    assert "If the suite does not pass, publish nothing" in prompt
    assert "Never force-push" in prompt
    assert "publish nothing beyond this branch" in prompt


@pytest.mark.parametrize("value", ["push", "pr"])
def test_the_grant_stays_a_bullet_of_the_scope_limits(value: str) -> None:
    """Wrapped under its bullet: a line at column 0 would read as a new section."""
    prompt = _issue_prompt(grants.parse(value))
    block = prompt.split("Scope limits:\n", 1)[1].split("\n- Do not touch", 1)[0]
    lines = block.splitlines()
    assert lines[0].startswith("- Do not merge.")
    assert all(line.startswith("  ") for line in lines[1:])
    assert all(len(line) <= 78 for line in lines)
    assert "fix/issue-317" in block  # a hyphenated branch is never split


def _piece(**kw) -> contracts.Piece:
    return contracts.Piece(slug="parser", files=["a.py"], **kw)


def test_a_piece_with_no_grant_ends_as_it_always_did() -> None:
    """The no-grant line is still byte-identical; only the closing clause follows.

    It stopped being the *last* line in 2026-09-16: every child, granted or
    not, is now told how to end itself, and that clause is rendered after the
    publish one. The permission wording it guards is unchanged.
    """
    prompt = dispatch.build_piece_prompt(_piece(), feature="f")
    publish = (
        "Run the full test suite before you finish. "
        "Do not merge or push without asking.\n"
    )
    assert publish in prompt
    assert prompt.endswith(publish + dispatch._closing_clause())


@pytest.mark.parametrize("value", ["push", "pr"])
def test_a_pieces_prompt_carries_its_grant(value: str) -> None:
    prompt = _flat(dispatch.build_piece_prompt(_piece(), feature="f",
                                               may=grants.parse(value)))
    assert "push `feat/f/parser` to origin" in prompt
    assert "do not ask for it again" in prompt
    assert "without asking." not in prompt
    # A piece closes no issue: there is none, and `Closes #` would name one.
    assert "Closes #" not in prompt
    assert ("open a pull request for it" in prompt) == (value == "pr")


# --- a contract's `may:` -------------------------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "c.md"
    path.write_text(
        "---\nfeature: demo\ncreated: 2026-01-01\nverdict: parallel\n---\n\n" + body,
        encoding="utf-8",
    )
    return path


def test_a_piece_may_name_its_grant(tmp_path: Path) -> None:
    contract = contracts.parse_contract(_write(tmp_path, """## one

- **files:** a.py
- **may:** pr

## two

- **files:** b.py

## three

- **files:** c.py
- **may:** none
"""))
    assert [p.may for p in contract.pieces] == [("push", "pr"), None, ()]


def test_a_contract_granting_merge_is_refused_before_any_tree(tmp_path: Path) -> None:
    with pytest.raises(contracts.ContractError, match="'one'.*never merges"):
        contracts.parse_contract(_write(tmp_path, """## one

- **files:** a.py
- **may:** merge
"""))


def test_the_example_contract_exercises_the_grant(tmp_path: Path) -> None:
    path = tmp_path / "example.md"
    path.write_text(contracts.EXAMPLE, encoding="utf-8")
    pieces = contracts.parse_contract(path).pieces
    assert any(p.may for p in pieces) and any(p.may is None for p in pieces)


@pytest.mark.parametrize(("own", "flag", "expected"), [
    (None, ("push",), ("push",)),         # absent: takes the flag
    (("push", "pr"), (), ("push", "pr")),  # the piece's own wins
    ((), ("push", "pr"), ()),              # an explicit none withholds the flag's
])
def test_a_pieces_own_grant_wins_over_the_flag(own, flag, expected) -> None:
    assert dispatch.piece_grant(_piece(may=own), flag) == expected


# --- the spawn records it -----------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    (root / "README.md").write_text("x\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "master"], ["add", "."], ["commit", "-qm", "i"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                       env={**__import__("os").environ, **env})
    return root


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(grants, "_default_vault", lambda: root)
    monkeypatch.setattr("mnemo.core.sessions.parents._default_vault", lambda: root)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: root)
    return root


def test_each_piece_is_spawned_with_its_resolved_grant(
    repo: Path, vault: Path, monkeypatch
) -> None:
    prompts: dict = {}

    def fake_spawn(prompt, *, cwd, model=None, lean=True, effort=None, read_only=False):
        prompts[Path(cwd).name] = prompt
        return {"proj-wt-c-one": "0000aaaa", "proj-wt-c-two": "0000bbbb"}[Path(cwd).name]

    monkeypatch.setattr(dispatch, "spawn_child", fake_spawn)
    monkeypatch.setattr(dispatch.claude_cli, "verify_registered", lambda *a, **k: None)
    contract = contracts.Contract(
        feature="f", verdict="parallel",
        pieces=[contracts.Piece(slug="one", files=["a.py"], may=("push", "pr")),
                contracts.Piece(slug="two", files=["b.py"])],
    )

    results = dispatch.dispatch_contract(contract, repo_root=repo, may=("push",))

    assert [r.may for r in results] == [("push", "pr"), ("push",)]
    assert "open a pull request for it" in _flat(prompts["proj-wt-c-one"])
    assert "Do not open a pull request" in _flat(prompts["proj-wt-c-two"])
    assert grants.read(vault) == {"0000aaaa": ("push", "pr"), "0000bbbb": ("push",)}


def test_an_issue_dispatch_with_no_grant_records_nothing(
    repo: Path, vault: Path, monkeypatch
) -> None:
    """No file is the honest `[]`, as the parent log's `null` is (#288)."""
    monkeypatch.setattr(dispatch, "spawn_child", lambda *a, **k: "a1b2c3d4")
    monkeypatch.setattr(dispatch.claude_cli, "verify_registered", lambda *a, **k: None)
    fetch = lambda n, **kw: dispatch.Issue(number=n, title="t", body="b")  # noqa: E731

    [result] = dispatch.dispatch_all([317], repo_root=repo, fetch=fetch)

    assert result.may == ()
    assert not grants.log_path(vault).exists()


def test_an_unwritable_vault_costs_the_record_not_the_dispatch(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    assert grants.record("a1b2c3d4", ("push",), vault_root=blocker) == ()


def test_a_forged_line_grants_nothing(vault: Path) -> None:
    """The queue must never show a right the dispatcher could not have given."""
    path = grants.log_path(vault)
    path.parent.mkdir(parents=True)
    path.write_text(
        "not json\n"
        + json.dumps({"short_id": "aaaa0001", "may": ["merge"]}) + "\n"
        + json.dumps({"short_id": "aaaa0002", "may": "push"}) + "\n"
        + json.dumps({"short_id": "aaaa0003", "may": ["pr"]}) + "\n",
        encoding="utf-8",
    )
    assert grants.read(vault) == {"aaaa0003": ("push", "pr")}


# --- the queue shows it --------------------------------------------------------


def test_sessions_json_carries_the_grant_on_every_row(
    vault: Path, monkeypatch, capsys
) -> None:
    from mnemo.cli.commands import sessions as sessions_cmd

    grants.record("child001", ("push", "pr"))
    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda root=None, *, cwd=None: [Session(short_id="child001"),
                                        Session(short_id="other002")],
    )

    code = sessions_cmd.cmd_sessions(argparse.Namespace(json=True, watch=False, **{"all": True}))

    assert code == 0
    rows = {r["short_id"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["child001"]["may"] == ["push", "pr"]
    assert rows["other002"]["may"] == []


def test_the_queue_names_the_children_that_will_publish_on_their_own() -> None:
    out = render_queue([
        Session(short_id="aaaa0001", state="working", tempo="active", may=("push", "pr")),
        Session(short_id="aaaa0002", state="working", tempo="active"),
        Session(short_id="aaaa0003", state="done", tempo="idle", may=("push",)),
    ])
    footer = [l for l in out.splitlines() if "may publish unasked" in l]
    # Only the one still able to act on it: a finished child's grant is spent.
    assert footer == ["  may publish unasked: aaaa0001 (push+pr)"]


def test_a_queue_with_no_grants_says_nothing_about_them() -> None:
    out = render_queue([Session(short_id="aaaa0001", state="working", tempo="active")])
    assert "publicam" not in out


# --- the command line ---------------------------------------------------------


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(
        **{"issues": [317], "dry_run": False, "contract": None, "model": None,
           "may": None, **kw}
    )


def test_the_may_flag_is_on_the_parser() -> None:
    from mnemo.cli.parser import _build_parser

    assert _build_parser().parse_args(["dispatch", "317", "--may", "pr"]).may == "pr"


def test_the_flag_reaches_the_dispatch_parsed(monkeypatch, capsys, tmp_path: Path) -> None:
    from mnemo.cli.commands import dispatch as dispatch_cmd

    seen: dict = {}
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        dispatch, "dispatch_all",
        lambda issues, *, repo_root, model=None, lean=True, may=(), effort=None, read_only=False: (
            seen.update(may=may),
            [dispatch.Dispatched(issue=317, worktree=tmp_path, short_id="a1b2c3d4",
                                 may=may)],
        )[1],
    )

    assert dispatch_cmd.cmd_dispatch(_args(may="pr")) == 0
    assert seen["may"] == ("push", "pr")
    assert "a1b2c3d4" in capsys.readouterr().out.split("may: push+pr")[0]


def test_a_merge_grant_is_refused_before_anything_runs(monkeypatch, capsys) -> None:
    from mnemo.cli.commands import dispatch as dispatch_cmd

    monkeypatch.setattr(dispatch_cmd, "_repo_root",
                        lambda: pytest.fail("refused before the repo is even read"))

    assert dispatch_cmd.cmd_dispatch(_args(may="merge")) == 1
    assert "never merges" in capsys.readouterr().out


def test_explicit_none_claims_no_grant(monkeypatch, capsys, tmp_path: Path) -> None:
    """The withheld run prints no `may:`; since 2026-09-16 that is `--may none`.

    It used to be the absent flag. The default inverted to `pr`, so the path
    that produces the empty grant moved — the behaviour it pins did not.
    """
    from mnemo.cli.commands import dispatch as dispatch_cmd

    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    assert dispatch_cmd.cmd_dispatch(_args(dry_run=True, may="none")) == 0
    assert "may" not in capsys.readouterr().out


def test_no_flag_now_claims_the_pr_grant(monkeypatch, capsys, tmp_path: Path) -> None:
    """Nothing said means the child publishes: the dry run says so out loud."""
    from mnemo.cli.commands import dispatch as dispatch_cmd

    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    assert dispatch_cmd.cmd_dispatch(_args(dry_run=True)) == 0
    assert "may: push+pr" in capsys.readouterr().out


def test_a_dry_run_shows_the_grant_each_piece_would_get(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    from mnemo.cli.commands import dispatch as dispatch_cmd

    path = _write(tmp_path, """## one

- **files:** a.py
- **may:** none

## two

- **files:** b.py
""")
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dispatch, "spawn_child",
                        lambda *a, **k: pytest.fail("a dry run must not spawn"))

    assert dispatch_cmd.cmd_dispatch(
        _args(issues=[], contract=str(path), dry_run=True, may="pr")
    ) == 0

    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert "may:" not in lines[0], lines[0]
    assert lines[1].endswith("may: push+pr"), lines[1]


# --- the default grant (2026-09-16) -------------------------------------------


def test_absent_may_flag_defaults_to_pr():
    """Nothing said means the child publishes (2026-09-16 design)."""
    from mnemo.cli.commands import dispatch as cmd

    assert cmd._default_grant(None) == ("push", "pr")


def test_explicit_none_still_withholds():
    """`--may none` is how a maintainer opts out; it must survive the default."""
    from mnemo.cli.commands import dispatch as cmd

    assert cmd._default_grant("none") == ()


def test_explicit_push_is_not_upgraded():
    from mnemo.cli.commands import dispatch as cmd

    assert cmd._default_grant("push") == ("push",)


def test_a_contract_piece_can_still_withhold_against_the_default():
    """`piece_grant` already resolves precedence; the new default flows
    through it as the flag's value, so `may: none` on a piece must still win."""
    from mnemo.cli.commands import dispatch as cmd

    default = cmd._default_grant(None)
    spike = contracts.Piece(slug="spike", files=["x.py"], may=())
    normal = contracts.Piece(slug="normal", files=["y.py"])

    assert dispatch.piece_grant(spike, default) == ()
    assert dispatch.piece_grant(normal, default) == ("push", "pr")
