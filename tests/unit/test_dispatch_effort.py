"""Which reasoning effort a dispatched child runs at, and how it is read back (#351).

Mirrors ``--model`` (#268) at every layer, measured on 2.1.273 rather than
assumed (``bg-effort-flag``):

1. ``claude --bg --effort <level>`` is accepted and lands in ``respawnFlags``
   as ``["--effort", "<level>", ...]`` — on a full-profile child and on a lean
   one alike.
2. Unlike the model, no default is ever resolved into the flags: a child
   spawned without ``--effort`` carries none, on either profile. ``None`` is
   the default, never an unreadable session.
3. The levels are a closed list the CLI prints itself, and an unknown one is a
   *warning* — the child silently runs on the default. So dispatch refuses a
   bad level before anything spawns, where the model path deliberately does
   not.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from mnemo.core import claude_cli, contracts, dispatch
from mnemo.core.sessions.jobs import Session, effort_from, read_sessions
from mnemo.core.sessions.render import render_queue

BG_STDOUT = (
    "backgrounded · \x1b[36ma1b2c3d4\x1b[39m\n"
    "\x1b[2m  claude agents             list sessions\x1b[22m\n"
    "\x1b[2m  claude attach a1b2c3d4    open in this terminal\x1b[22m\n"
    "\x1b[2m  claude logs a1b2c3d4      show recent output\x1b[22m\n"
    "\x1b[2m  claude stop a1b2c3d4      stop this session\x1b[22m\n"
)


def _capture(monkeypatch) -> dict:
    seen: dict = {}

    def fake_run(args, **kwargs):
        seen["args"] = list(args)
        return subprocess.CompletedProcess(args, 0, stdout=BG_STDOUT, stderr="")

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)
    return seen


def test_the_levels_are_the_ones_the_cli_prints() -> None:
    """Copied from ``claude --effort bogus --version`` on 2.1.273, in its order."""
    assert claude_cli.EFFORT_LEVELS == ("low", "medium", "high", "xhigh", "max")


def test_the_assumption_is_registered() -> None:
    found = claude_cli.assumption("bg-effort-flag")
    assert "Session.effort" in found.used_by
    assert "spawn_child" in found.used_by


# --- the flag reaches the CLI ----------------------------------------------


@pytest.mark.real_spawn
def test_an_effort_is_passed_through_as_the_effort_flag(tmp_path: Path, monkeypatch) -> None:
    seen = _capture(monkeypatch)

    dispatch.spawn_child("do the thing", cwd=tmp_path, effort="high", lean=False)

    args = seen["args"]
    assert args[:2] == ["claude", "--bg"]
    assert args[args.index("--effort") + 1] == "high"
    assert args[-1] == "do the thing"


@pytest.mark.real_spawn
def test_effort_composes_with_model_and_the_lean_profile(tmp_path: Path, monkeypatch) -> None:
    seen = _capture(monkeypatch)

    dispatch.spawn_child("x", cwd=tmp_path, model="haiku", effort="max")

    args = seen["args"]
    assert args[args.index("--model") + 1] == "haiku"
    assert args[args.index("--effort") + 1] == "max"
    assert "--strict-mcp-config" in args  # the lean profile is still there
    assert args[-1] == "x"


@pytest.mark.real_spawn
@pytest.mark.parametrize("effort", [None, ""], ids=["none", "empty"])
def test_no_effort_spawns_the_command_it_spawned_before(
    tmp_path: Path, monkeypatch, effort
) -> None:
    """Byte-identical, not merely equivalent: no ``--effort`` in any spelling."""
    seen = _capture(monkeypatch)

    dispatch.spawn_child("do the thing", cwd=tmp_path, lean=False, effort=effort)

    assert seen["args"] == ["claude", "--bg", "do the thing"]


@pytest.mark.real_spawn
@pytest.mark.parametrize("effort", ["bogus", "HIGH", "hi", "high "])
def test_an_unknown_level_is_refused_before_claude_runs(
    tmp_path: Path, monkeypatch, effort
) -> None:
    """The CLI would only warn and run the default; the caller would never know."""
    monkeypatch.setattr(
        dispatch.subprocess, "run", lambda *a, **k: pytest.fail("claude was run")
    )
    with pytest.raises(dispatch.DispatchError, match="effort"):
        dispatch.spawn_child("x", cwd=tmp_path, effort=effort)


# --- the flag reaches every child ------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    import os

    root = tmp_path / "proj"
    root.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    (root / "README.md").write_text("x\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "master"], ["add", "."], ["commit", "-qm", "i"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)
    return root


def _fetch(issue: int, *, repo_root):
    return dispatch.Issue(number=issue, title="t", body="b")


def _recording(monkeypatch) -> list:
    spawned: list = []

    def fake_spawn(prompt, *, cwd, model=None, lean=True, effort=None):
        spawned.append(effort)
        return "a1b2c3d4"

    monkeypatch.setattr(dispatch, "spawn_child", fake_spawn)
    monkeypatch.setattr(dispatch.claude_cli, "verify_registered", lambda *a, **k: None)
    return spawned


def test_every_child_of_one_dispatch_gets_the_effort(repo: Path, monkeypatch) -> None:
    spawned = _recording(monkeypatch)
    results = dispatch.dispatch_all(
        [351, 352], repo_root=repo, fetch=_fetch, effort="high"
    )
    assert spawned == ["high", "high"]
    assert [r.effort for r in results] == ["high", "high"]


def test_a_dispatch_with_no_effort_records_none(repo: Path, monkeypatch) -> None:
    spawned = _recording(monkeypatch)
    result = dispatch.dispatch_issue(351, repo_root=repo, fetch=_fetch)
    assert spawned == [None]
    assert result.effort is None


def _contract(tmp_path: Path, *, efforts):
    return contracts.Contract(
        feature="demo",
        verdict="parallel",
        pieces=[
            contracts.Piece(slug=f"p{i}", files=[f"{i}.py"], exposes=["`f()`"],
                            effort=effort)
            for i, effort in enumerate(efforts)
        ],
        path=tmp_path / "contract.md",
    )


def test_a_pieces_own_effort_wins_over_the_flag(repo: Path, monkeypatch) -> None:
    """``effort: max`` on a piece beats ``--effort high`` — the acceptance case."""
    spawned = _recording(monkeypatch)
    dispatch.dispatch_contract(
        _contract(repo, efforts=["max", None]), repo_root=repo, effort="high"
    )
    assert spawned == ["max", "high"]


# --- parsing `effort:` out of a contract -----------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "c.md"
    path.write_text(
        "---\nfeature: demo\ncreated: 2026-01-01\nverdict: parallel\n---\n\n" + body,
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("level", claude_cli.EFFORT_LEVELS)
def test_a_piece_may_name_its_effort(tmp_path: Path, level: str) -> None:
    path = _write(tmp_path, f"## one\n\n- **files:** a.py\n- **effort:** {level}\n")
    assert contracts.parse_contract(path).pieces[0].effort == level


def test_the_effort_line_is_optional_and_does_not_leak(tmp_path: Path) -> None:
    path = _write(tmp_path, """## one

- **files:** a.py
- **effort:** max

## two

- **files:** b.py
""")
    assert [p.effort for p in contracts.parse_contract(path).pieces] == ["max", None]


@pytest.mark.parametrize("value", ["bogus", "high, but max if it gets hard", "HIGH"])
def test_an_unknown_effort_in_a_contract_is_refused(tmp_path: Path, value: str) -> None:
    path = _write(tmp_path, f"## one\n\n- **files:** a.py\n- **effort:** {value}\n")
    with pytest.raises(contracts.ContractError, match="effort"):
        contracts.parse_contract(path)


def test_the_example_contract_exercises_the_field() -> None:
    assert "- **effort:**" in contracts.EXAMPLE
    # And the example still parses, so the line is in the grammar.
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "c.md"
        path.write_text(contracts.EXAMPLE, encoding="utf-8")
        pieces = contracts.parse_contract(path).pieces
    assert [p.effort for p in pieces] == ["medium", None]


# --- reading it back off state.json ----------------------------------------


def test_the_effort_is_read_from_respawn_flags() -> None:
    """The flags exactly as a full-profile child of 2.1.273 recorded them."""
    data = {"respawnFlags": ["--effort", "high", "--model", "opus[1m]"]}
    assert effort_from(data) == "high"


def test_a_lean_childs_effort_is_read_too() -> None:
    """The flags exactly as a lean-shaped child of 2.1.273 recorded them."""
    data = {"respawnFlags": [
        "--effort", "low", "--setting-sources", "project,local",
        "--strict-mcp-config", "--mcp-config", "/x/m.json", "--settings", "/x/s.json",
    ]}
    assert effort_from(data) == "low"


@pytest.mark.parametrize(
    "data",
    [{}, {"respawnFlags": None}, {"respawnFlags": []}, {"respawnFlags": ["--effort"]},
     {"respawnFlags": ["--model", "haiku", "--permission-mode", "default"]}],
    ids=["absent", "null", "empty", "truncated", "model-only"],
)
def test_a_session_without_the_flag_has_no_effort(data) -> None:
    assert effort_from(data) is None


def test_an_unknown_recorded_level_is_still_reported() -> None:
    """Reading is not validating: a newer CLI's level is still the truth."""
    assert effort_from({"respawnFlags": ["--effort", "ultra"]}) == "ultra"


def test_read_sessions_stamps_the_effort(tmp_path: Path) -> None:
    for short_id, flags in (("a1b2c3d4", ["--effort", "max"]), ("b1b2c3d4", [])):
        entry = tmp_path / short_id
        entry.mkdir()
        (entry / "state.json").write_text(json.dumps({
            "state": "working", "tempo": "active", "cwd": str(tmp_path),
            "respawnFlags": flags,
        }), encoding="utf-8")

    found = {s.short_id: s.effort for s in read_sessions(tmp_path)}
    assert found == {"a1b2c3d4": "max", "b1b2c3d4": None}


def test_sessions_json_carries_the_effort(monkeypatch, capsys) -> None:
    """Through ``mnemo sessions --json``: the level, and ``null`` when unset."""
    from mnemo.cli.commands import sessions as sessions_cmd

    found = [
        Session(short_id="a1b2c3d4", state="done", tempo="idle", effort="max"),
        Session(short_id="b1b2c3d4", state="done", tempo="idle"),
    ]
    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda root=None, *, cwd=None: found,
    )
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep",
                        lambda sessions, *, vault_root: 0)
    args = argparse.Namespace(json=True, watch=False, **{"all": True})

    assert sessions_cmd.cmd_sessions(args) == 0
    rows = {r["short_id"]: r["effort"] for r in json.loads(capsys.readouterr().out)}
    assert rows == {"a1b2c3d4": "max", "b1b2c3d4": None}


# --- showing it ------------------------------------------------------------


def _session(short_id: str, effort: str | None) -> Session:
    return Session(short_id=short_id, state="working", tempo="active", effort=effort)


def test_a_queue_on_the_default_says_nothing_about_effort() -> None:
    """The common case — no child chose one — is not rendered as anything."""
    assert "effort" not in render_queue([_session("a" * 8, None), _session("b" * 8, None)])


def test_a_queue_with_a_chosen_effort_names_it_and_counts_the_default() -> None:
    out = render_queue([_session("a" * 8, "max"), _session("b" * 8, None),
                        _session("c" * 8, None)])
    line = next(l for l in out.splitlines() if "effort:" in l)
    assert line == "  effort: default ×2, max"


# --- the command line ------------------------------------------------------


def _args(**kw):
    return argparse.Namespace(
        **{"issues": [351], "dry_run": False, "contract": None, "model": None,
           "effort": None, **kw}
    )


def test_the_effort_flag_is_on_the_parser() -> None:
    from mnemo.cli.parser import _build_parser

    args = _build_parser().parse_args(["dispatch", "351", "--effort", "high"])
    assert args.effort == "high"
    assert _build_parser().parse_args(["dispatch", "351"]).effort is None


def test_the_parser_refuses_an_unknown_level(capsys) -> None:
    from mnemo.cli.parser import _build_parser

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["dispatch", "351", "--effort", "hgih"])
    err = capsys.readouterr().err
    assert "--effort" in err and "xhigh" in err


def test_the_command_refuses_an_unknown_level_before_spawning(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    from mnemo.cli.commands import dispatch as dispatch_cmd

    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dispatch, "dispatch_all", lambda *a, **k: pytest.fail("dispatched"))

    assert dispatch_cmd.cmd_dispatch(_args(effort="bogus")) == 1
    assert "--effort" in capsys.readouterr().out


def test_the_flag_reaches_the_dispatch_and_the_report(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    from mnemo.cli.commands import dispatch as dispatch_cmd

    seen: dict = {}
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        dispatch, "dispatch_all",
        lambda issues, *, repo_root, model=None, lean=True, may=(), effort=None: (
            seen.update(effort=effort),
            [dispatch.Dispatched(issue=351, worktree=tmp_path, short_id="a1b2c3d4",
                                 model=model, effort=effort)],
        )[1],
    )

    assert dispatch_cmd.cmd_dispatch(_args(model="haiku", effort="high")) == 0
    assert seen["effort"] == "high"
    assert "[haiku, effort high]" in capsys.readouterr().out


def test_a_dry_run_shows_the_effort_each_piece_would_get(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    from mnemo.cli.commands import dispatch as dispatch_cmd

    path = _write(tmp_path, """## one

- **files:** a.py
- **effort:** max

## two

- **files:** b.py
""")
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dispatch, "spawn_child", lambda *a, **k: pytest.fail("spawned"))

    assert dispatch_cmd.cmd_dispatch(
        _args(issues=[], contract=str(path), dry_run=True, effort="high")
    ) == 0
    lines = [l.split("  may: ")[0]
             for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert lines[0].endswith("[effort max]"), lines[0]
    assert lines[1].endswith("[effort high]"), lines[1]
