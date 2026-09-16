"""Which model a dispatched child runs on, and how it is read back (#268).

Before this, ``spawn_child`` ran ``claude --bg <prompt>`` with no ``--model``,
so every child took whatever ``~/.claude/settings.json`` resolved to and the
parent had no say. Measured on 2026-09-14: 23 real children, ``model: null``
in every ``state.json``, and one transcript of 135 assistant turns all on
``claude-fable-5-1`` — the machine default, chosen by nobody.

Two claims are load-bearing here and both were measured against the installed
binary (2.1.270) rather than assumed:

1. ``--bg`` and ``--model`` compose. A child spawned with ``--model haiku``
   produced a transcript whose every assistant record reads
   ``claude-haiku-4-5-20251001``.
2. The model is readable back off ``state.json`` — but under ``respawnFlags``,
   **not** the sibling ``model`` key, which was ``null`` on all 23 real
   sessions including the ``--model haiku`` one. The issue's premise that
   "``state.json`` says nothing" is wrong in that detail; the transcript is
   not the only source, and it is the more expensive one.

The default path is asserted as strictly as the flag path: a dispatch that
names no model must still add no `--model` in any spelling, and on the full
profile (#270) run the byte-identical command it ran before.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mnemo.core import contracts, dispatch
from mnemo.core.sessions.jobs import Session, model_from, read_sessions
from mnemo.core.sessions.render import render_queue

# The same verbatim block the rest of the dispatch suite parses; duplicated
# rather than imported so this module states the shape it depends on.
BG_STDOUT = (
    "backgrounded · \x1b[36ma1b2c3d4\x1b[39m\n"
    "\x1b[2m  claude agents             list sessions\x1b[22m\n"
    "\x1b[2m  claude attach a1b2c3d4    open in this terminal\x1b[22m\n"
    "\x1b[2m  claude logs a1b2c3d4      show recent output\x1b[22m\n"
    "\x1b[2m  claude stop a1b2c3d4      stop this session\x1b[22m\n"
)


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


def _capture(monkeypatch) -> dict:
    """Record the argv ``spawn_child`` builds, without running claude."""
    seen: dict = {}

    def fake_run(args, **kwargs):
        seen["args"] = list(args)
        return subprocess.CompletedProcess(args, 0, stdout=BG_STDOUT, stderr="")

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)
    return seen


# --- the flag reaches the CLI ----------------------------------------------


@pytest.mark.real_spawn
def test_a_model_is_passed_through_as_the_model_flag(repo: Path, monkeypatch) -> None:
    seen = _capture(monkeypatch)
    tree = dispatch.ensure_worktree(268, repo_root=repo)

    dispatch.spawn_child("do the thing", cwd=tree, model="haiku")

    args = seen["args"]
    assert args[:2] == ["claude", "--bg"]
    assert "--model" in args
    assert args[args.index("--model") + 1] == "haiku"
    # Still positional, still last: the prompt must not be shifted into the
    # flag's value slot, which is exactly what inserting an argument can do.
    assert args[-1] == "do the thing"


@pytest.mark.real_spawn
def test_no_model_spawns_the_command_it_spawned_before(repo: Path, monkeypatch) -> None:
    """The unchanged case must stay byte-identical, not merely equivalent.

    A dispatch that names no model has to keep resolving the machine default.
    Passing ``--model ""`` or ``--model default`` would both be *plausible*
    and both wrong: the first is a CLI error, the second names a model that
    does not exist.
    """
    seen = _capture(monkeypatch)
    tree = dispatch.ensure_worktree(268, repo_root=repo)

    # `lean=False` isolates the model question from the profile question
    # (#270, which adds its own flags by default and pins this same
    # byte-identical shape in test_spawn_full_profile_adds_no_flags). The two
    # are orthogonal: this is the one arm where neither contributes anything,
    # so a stray token here can only have come from the model path.
    dispatch.spawn_child("do the thing", cwd=tree, lean=False)

    assert seen["args"] == ["claude", "--bg", "do the thing"]

    # And under the lean default, the *model* still adds nothing: the profile
    # flags are there, no `--model` in any spelling is.
    dispatch.spawn_child("do the thing", cwd=tree)

    assert "--model" not in seen["args"]
    assert "default" not in seen["args"] and "" not in seen["args"]
    assert seen["args"][-1] == "do the thing"


@pytest.mark.real_spawn
@pytest.mark.parametrize("model", [None, ""], ids=["none", "empty"])
def test_a_falsy_model_adds_no_flag(repo: Path, monkeypatch, model) -> None:
    """An empty string is "not chosen", never ``--model ''``.

    ``argparse`` hands back ``None`` for an absent flag, but a shell quoting
    accident (``--model ""``) produces the empty string, and forwarding it
    would make claude refuse a dispatch that looked fine on the command line.
    """
    seen = _capture(monkeypatch)
    tree = dispatch.ensure_worktree(268, repo_root=repo)

    dispatch.spawn_child("x", cwd=tree, model=model)

    assert "--model" not in seen["args"]


# --- the flag reaches every child ------------------------------------------


def _fetch(issue: int, *, repo_root):
    return dispatch.Issue(number=issue, title="t", body="b")


def test_every_child_of_one_dispatch_gets_the_model(repo: Path, monkeypatch) -> None:
    spawned: list = []

    def fake_spawn(prompt, *, cwd, model=None, lean=True):
        spawned.append(model)
        return "a1b2c3d4"

    monkeypatch.setattr(dispatch, "spawn_child", fake_spawn)
    results = dispatch.dispatch_all(
        [268, 269], repo_root=repo, fetch=_fetch, model="haiku"
    )

    assert spawned == ["haiku", "haiku"]
    # Recorded on the result too, so the report can say what was chosen at the
    # moment of choosing — before any state.json exists to read it back from.
    assert [r.model for r in results] == ["haiku", "haiku"]


def test_a_dispatch_with_no_model_records_none(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        dispatch, "spawn_child", lambda prompt, *, cwd, model=None, lean=True: "a1b2c3d4"
    )
    result = dispatch.dispatch_issue(268, repo_root=repo, fetch=_fetch)
    assert result.model is None


# --- a contract piece names its own ----------------------------------------


def _contract(tmp_path: Path, *, models):
    return contracts.Contract(
        feature="demo",
        verdict="parallel",
        pieces=[
            contracts.Piece(slug=f"p{i}", files=[f"{i}.py"], exposes=["`f()`"],
                            model=model)
            for i, model in enumerate(models)
        ],
        path=tmp_path / "contract.md",
    )


def test_a_pieces_own_model_wins_over_the_flag(repo: Path, monkeypatch) -> None:
    """The contract was reviewed; the flag is a blanket. The contract wins.

    The other direction would make the field unusable: a maintainer who set
    ``--model opus`` for one hard piece would silently upgrade the three
    mechanical ones the contract had already priced.
    """
    spawned: list = []

    def fake_spawn(prompt, *, cwd, model=None, lean=True):
        spawned.append(model)
        return "a1b2c3d4"

    monkeypatch.setattr(dispatch, "spawn_child", fake_spawn)
    dispatch.dispatch_contract(
        _contract(repo, models=["haiku", None]), repo_root=repo, model="opus"
    )

    assert spawned == ["haiku", "opus"]


def test_a_contract_with_no_models_takes_the_flag(repo: Path, monkeypatch) -> None:
    spawned: list = []
    monkeypatch.setattr(
        dispatch, "spawn_child",
        lambda prompt, *, cwd, model=None, lean=True: (spawned.append(model), "a1b2c3d4")[1],
    )
    dispatch.dispatch_contract(
        _contract(repo, models=[None, None]), repo_root=repo, model="sonnet"
    )
    assert spawned == ["sonnet", "sonnet"]


# --- parsing `model:` out of a contract ------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "c.md"
    path.write_text(
        "---\nfeature: demo\ncreated: 2026-01-01\nverdict: parallel\n---\n\n" + body,
        encoding="utf-8",
    )
    return path


def test_a_piece_may_name_its_model(tmp_path: Path) -> None:
    path = _write(tmp_path, """## storage

- **files:** a.py
- **exposes:** `f()`
- **model:** haiku
""")
    contract = contracts.parse_contract(path)
    assert contract.pieces[0].model == "haiku"


def test_the_model_line_is_optional(tmp_path: Path) -> None:
    """Every contract written before the field must still parse unchanged."""
    path = _write(tmp_path, """## storage

- **files:** a.py
- **exposes:** `f()`
""")
    assert contracts.parse_contract(path).pieces[0].model is None


def test_a_model_does_not_leak_into_the_next_piece(tmp_path: Path) -> None:
    """The accumulator is reset per heading, like files/exposes/consumes.

    The classic parser bug for a scalar field: lists are visibly re-bound at
    each ``##`` and a lone string is easy to forget, so the second piece
    silently inherits the first one's budget.
    """
    path = _write(tmp_path, """## one

- **files:** a.py
- **model:** haiku

## two

- **files:** b.py
""")
    pieces = contracts.parse_contract(path).pieces
    assert [p.model for p in pieces] == ["haiku", None]


@pytest.mark.parametrize(
    "value",
    ["haiku", "opus", "claude-haiku-4-5-20251001", "opus[1m]", "claude-fable-5-1[1m]"],
)
def test_real_model_spellings_are_admitted(tmp_path: Path, value: str) -> None:
    """Aliases, full ids, and the ``[1m]`` suffix real sessions actually carry.

    ``claude-fable-5-1[1m]`` is not hypothetical: it is what ``respawnFlags``
    recorded for 18 of the 23 children measured on 2026-09-14. A validator
    that refused it would refuse the machine's own default.
    """
    path = _write(tmp_path, f"## one\n\n- **files:** a.py\n- **model:** {value}\n")
    assert contracts.parse_contract(path).pieces[0].model == value


@pytest.mark.parametrize(
    "value",
    [
        "haiku, but use opus if it gets hard",
        "use a regex to parse it",
        "haiku; rm -rf /",
    ],
)
def test_prose_under_model_is_refused(tmp_path: Path, value: str) -> None:
    """``model`` is a budget, and a budget is one token.

    The same window ``files`` and ``exposes`` are checked through: every field
    is quoted into a child's prompt or handed to a subprocess, so an unchecked
    one is an unchecked instruction.
    """
    path = _write(tmp_path, f"## one\n\n- **files:** a.py\n- **model:** {value}\n")
    with pytest.raises(contracts.ContractError, match="model"):
        contracts.parse_contract(path)


def test_the_example_contract_exercises_the_field() -> None:
    """``--example`` is the format's documentation; it must show this one.

    The example is parsed back by the contracts suite, so a ``model:`` line
    there cannot drift out of the grammar the way a prose description can.
    """
    assert "- **model:**" in contracts.EXAMPLE


# --- reading it back off state.json ----------------------------------------


def test_the_model_is_read_from_respawn_flags() -> None:
    assert model_from({"respawnFlags": ["--model", "haiku"]}) == "haiku"


def test_the_null_model_key_is_not_read() -> None:
    """Measured: ``model`` is null on all 23 real sessions, ``respawnFlags`` is not.

    Falling back to the ``model`` key "when it is set" would be a trap rather
    than a fallback — it is the field most likely to start carrying something
    *different* from what the child is running on.
    """
    data = {"model": None, "respawnFlags": ["--model", "claude-fable-5-1[1m]"]}
    assert model_from(data) == "claude-fable-5-1[1m]"


def test_a_model_flag_with_no_value_yields_none() -> None:
    """A truncated flag list is missing data, not a model named ''."""
    assert model_from({"respawnFlags": ["--model"]}) is None


@pytest.mark.parametrize(
    "data",
    [{}, {"respawnFlags": None}, {"respawnFlags": []},
     {"respawnFlags": ["--permission-mode", "default"]}],
    ids=["absent", "null", "empty", "no-model-flag"],
)
def test_a_session_without_the_flag_has_no_model(data) -> None:
    assert model_from(data) is None


def test_read_sessions_stamps_the_model(tmp_path: Path) -> None:
    """End to end through the reader every consumer goes through."""
    import json

    entry = tmp_path / "a1b2c3d4"
    entry.mkdir()
    (entry / "state.json").write_text(json.dumps({
        "state": "working", "tempo": "active", "cwd": str(tmp_path),
        "respawnFlags": ["--model", "haiku", "--permission-mode", "default"],
    }), encoding="utf-8")

    found = read_sessions(tmp_path)
    assert [s.model for s in found] == ["haiku"]


# --- showing it ------------------------------------------------------------


def _session(short_id: str, model: str | None) -> Session:
    return Session(short_id=short_id, state="working", tempo="active", model=model)


def test_the_queue_names_the_models_in_play() -> None:
    out = render_queue([_session("a" * 8, "opus[1m]"), _session("b" * 8, "haiku")])
    assert "modelos:" in out
    assert "opus[1m]" in out and "haiku" in out


def test_a_uniform_queue_still_says_so() -> None:
    """A line that vanishes when the answer is uniform cannot be trusted absent."""
    out = render_queue([_session("a" * 8, "haiku"), _session("b" * 8, "haiku")])
    assert "haiku ×2" in out


def test_the_majority_model_is_named_first() -> None:
    """The outlier reads at the end of the line, where the eye lands last."""
    sessions = [_session(f"{i}" * 8, "opus") for i in range(3)]
    sessions.append(_session("z" * 8, "haiku"))
    line = next(l for l in render_queue(sessions).splitlines() if "modelos:" in l)
    assert line.index("opus") < line.index("haiku")


def test_a_queue_that_records_no_model_says_nothing() -> None:
    """An older Claude Code writes no ``respawnFlags``; no line beats a blank one."""
    assert "modelos:" not in render_queue([_session("a" * 8, None)])


def test_the_model_does_not_become_a_column() -> None:
    """One footer, not one cell per row — the width is measured, not stylistic.

    Real rows already run 87-135 columns; the same model repeated on nearly
    every row would add up to 20 more and say nothing new. Asserted because
    "add a column" is the obvious next change, and it is the wrong one.
    """
    sessions = [_session("a" * 8, "claude-fable-5-1[1m]") for _ in range(3)]
    out = render_queue(sessions)
    rows = [l for l in out.splitlines() if l.startswith("  aaaaaaaa")]
    assert rows and all("claude-fable-5-1" not in row for row in rows)


# --- the command line ------------------------------------------------------


def _args(**kw):
    import argparse

    return argparse.Namespace(
        **{"issues": [268], "dry_run": False, "contract": None, "model": None, **kw}
    )


def test_the_flag_reaches_the_dispatch(monkeypatch, tmp_path: Path) -> None:
    from mnemo.cli.commands import dispatch as dispatch_cmd

    seen: dict = {}
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        dispatch, "dispatch_all",
        lambda issues, *, repo_root, model=None, lean=True, may=(): (
            seen.update(model=model),
            [dispatch.Dispatched(issue=268, worktree=tmp_path, short_id="a1b2c3d4")],
        )[1],
    )

    assert dispatch_cmd.cmd_dispatch(_args(model="haiku")) == 0
    assert seen["model"] == "haiku"


def test_the_report_says_which_model_was_chosen(monkeypatch, capsys, tmp_path: Path) -> None:
    from mnemo.cli.commands import dispatch as dispatch_cmd

    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        dispatch, "dispatch_all",
        lambda issues, *, repo_root, model=None, lean=True, may=(): [
            dispatch.Dispatched(issue=268, worktree=tmp_path, short_id="a1b2c3d4",
                                model=model)
        ],
    )

    dispatch_cmd.cmd_dispatch(_args(model="haiku"))
    assert "[haiku]" in capsys.readouterr().out


def test_a_dispatch_with_no_model_claims_no_choice(monkeypatch, capsys, tmp_path: Path) -> None:
    """No ``[default]`` placeholder: the unchanged case must read unchanged."""
    from mnemo.cli.commands import dispatch as dispatch_cmd

    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        dispatch, "dispatch_all",
        lambda issues, *, repo_root, model=None, lean=True, may=(): [
            dispatch.Dispatched(issue=268, worktree=tmp_path, short_id="a1b2c3d4")
        ],
    )

    dispatch_cmd.cmd_dispatch(_args())
    assert "[" not in capsys.readouterr().out


def test_a_dry_run_shows_the_model_each_piece_would_get(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """A dry run is where the flag is checked before it is spent.

    The piece's own model wins here exactly as it will at spawn — a dry run
    that showed the flag for every piece would be lying about the one thing
    the maintainer is running it to see.
    """
    from mnemo.cli.commands import dispatch as dispatch_cmd

    path = _write(tmp_path, """## one

- **files:** a.py
- **model:** haiku

## two

- **files:** b.py
""")
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        dispatch, "spawn_child",
        lambda *a, **k: pytest.fail("a dry run must not spawn"),
    )

    assert dispatch_cmd.cmd_dispatch(
        _args(issues=[], contract=str(path), dry_run=True, model="opus")
    ) == 0

    # The grant suffix trails the model tag since `--may` began defaulting to
    # `pr` (2026-09-16); stripping it keeps this about the model column.
    lines = [l.split("  may: ")[0]
             for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert lines[0].endswith("[haiku]"), lines[0]
    assert lines[1].endswith("[opus]"), lines[1]


def test_the_model_flag_is_on_the_parser() -> None:
    """Parsed, not only read off the namespace by ``getattr``."""
    from mnemo.cli.parser import _build_parser

    args = _build_parser().parse_args(["dispatch", "268", "--model", "haiku"])
    assert args.model == "haiku"
