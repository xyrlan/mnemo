"""An advisor model for dispatched children, off unless named (#818).

Mirrors ``--model`` (#268) and ``--effort`` (#351) at every layer, on what was
measured on 2.1.321 rather than assumed (``bg-advisor-flag``):

1. ``claude --bg --advisor <model>`` reaches a lean child and lands in
   ``respawnFlags`` as ``["--advisor", "<model>", ...]``.
2. An advisor ranked below the child's model is dropped without a word — the
   child starts, keeps the flag, and has no advisor tool. So dispatch refuses
   a pairing it can prove inverted before anything spawns.
3. Omitted, the argv and the settings file are byte-identical to before.

And the measurement half: ``--twins --advisor`` runs one pair as two arms.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest

from mnemo.core import claude_cli, contracts, dispatch, twins
from mnemo.core.sessions.jobs import Session, advisor_from, read_sessions
from tests.unit import _git_template

BG_STDOUT = (
    "backgrounded · \x1b[36ma1b2c3d4\x1b[39m\n"
    "\x1b[2m  claude attach a1b2c3d4    open in this terminal\x1b[22m\n"
)

#: The lean child's flags exactly as Claude Code recorded them on 2.1.321
#: (the part 1 probe through `spawn_child`): it moves `--advisor` first.
LEAN_ADVISED_FLAGS = [
    "--advisor", "opus", "--setting-sources", "project,local", "--strict-mcp-config",
    "--mcp-config", "/x/.mnemo-child-profile/mcp.json",
    "--settings", "/x/.mnemo-child-profile/settings.json", "--model", "sonnet",
]


def _capture(monkeypatch) -> dict:
    seen: dict = {}

    def fake_run(args, **kwargs):
        seen["args"] = list(args)
        return subprocess.CompletedProcess(args, 0, stdout=BG_STDOUT, stderr="")

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)
    return seen


def test_the_assumption_is_registered() -> None:
    found = claude_cli.assumption("bg-advisor-flag")
    assert "spawn_child" in found.used_by and "Session.advisor" in found.used_by
    assert found.verified.startswith("2.1.321")
    assert "dropped" in found.claim and "advisor_message" in found.claim


# --- the pairing dispatch can prove inverted --------------------------------


@pytest.mark.parametrize(("model", "advisor", "below"), [
    ("opus", "sonnet", True),
    ("fable", "opus", True),
    ("sonnet", "haiku", True),
    ("opus[1m]", "sonnet", True),         # a context suffix is the same model
    ("OPUS", "Sonnet", True),
    ("sonnet", "opus", False),
    ("sonnet", "sonnet", False),
    ("haiku", "fable", False),
    ("claude-opus-5-5", "sonnet", False),  # a full id is never ranked: Opus 4.8
    ("opus", "claude-sonnet-4-6", False),  # takes Sonnet 5.5, Opus 5.5 does not
    (None, "haiku", False),                # the machine's default: unknown rank
    ("sonnet", None, False),
])
def test_only_two_aliases_in_the_wrong_order_are_provably_below(model, advisor, below) -> None:
    assert claude_cli.advisor_ranks_below(model, advisor) is below


@pytest.mark.parametrize("advisor", ["opus please", "opus;rm", "`opus`", "", "op us"])
def test_an_advisor_is_one_model_token(advisor) -> None:
    if advisor == "":
        dispatch.check_advisor("sonnet", None)   # absent is fine
        return
    with pytest.raises(dispatch.DispatchError, match="not a model id"):
        dispatch.check_advisor("sonnet", advisor)


def test_a_full_id_or_a_suffix_is_one_token() -> None:
    for advisor in ("claude-opus-5-5", "opus[1m]", "fable"):
        dispatch.check_advisor("sonnet", advisor)


# --- reading a transcript's usage -------------------------------------------


def test_split_usage_separates_the_advisors_iterations() -> None:
    usage = {"input_tokens": 4, "output_tokens": 45, "iterations": [
        {"type": "message", "input_tokens": 2, "output_tokens": 26},
        {"type": "advisor_message", "model": "claude-opus-5-5",
         "input_tokens": 51631, "output_tokens": 426},
        {"type": "message", "input_tokens": 2, "output_tokens": 19},
    ]}
    own, advisor = claude_cli.split_usage(usage)
    assert [e["output_tokens"] for e in own] == [26, 19]
    assert [e["model"] for e in advisor] == ["claude-opus-5-5"]
    plain = {"input_tokens": 3, "output_tokens": 7}
    assert claude_cli.split_usage(plain) == ((plain,), ())
    assert claude_cli.split_usage(None) == ((), ())


# --- the flag reaches the CLI -----------------------------------------------


@pytest.mark.real_spawn
def test_an_advisor_is_passed_right_after_the_model(tmp_path: Path, monkeypatch) -> None:
    seen = _capture(monkeypatch)
    dispatch.spawn_child("x", cwd=tmp_path, model="sonnet", advisor="opus", effort="high")
    args = seen["args"]
    at = args.index("--model")
    assert args[at:at + 4] == ["--model", "sonnet", "--advisor", "opus"]
    assert args[args.index("--effort") + 1] == "high"
    assert "--strict-mcp-config" in args and args[-1] == "x"


@pytest.mark.real_spawn
@pytest.mark.parametrize("advisor", [None, ""], ids=["none", "empty"])
def test_no_advisor_spawns_the_command_it_spawned_before(
    tmp_path: Path, monkeypatch, advisor
) -> None:
    seen = _capture(monkeypatch)
    monkeypatch.setattr("mnemo.core.child_priority.supported", lambda: False)
    dispatch.spawn_child("do the thing", cwd=tmp_path, lean=False, advisor=advisor)
    assert seen["args"] == ["claude", "--bg", "do the thing"]


@pytest.mark.real_spawn
def test_the_advisor_never_touches_the_settings_file(tmp_path: Path, monkeypatch) -> None:
    """The flag route: the lean child's settings file is the same either way."""
    _capture(monkeypatch)
    settings = tmp_path / "t" / ".mnemo-child-profile" / "settings.json"
    (tmp_path / "t").mkdir()
    extra = {"autoMemoryEnabled": False}

    dispatch.spawn_child("x", cwd=tmp_path / "t", model="sonnet", settings=extra)
    without = settings.read_bytes()
    dispatch.spawn_child("x", cwd=tmp_path / "t", model="sonnet", settings=extra,
                         advisor="opus")
    assert settings.read_bytes() == without
    assert b"advisor" not in without


@pytest.mark.real_spawn
@pytest.mark.parametrize(("model", "advisor"), [("opus", "sonnet"), ("sonnet", "op us")])
def test_a_dropped_advisor_is_refused_before_claude_runs(
    tmp_path: Path, monkeypatch, model, advisor
) -> None:
    monkeypatch.setattr(dispatch.subprocess, "run", lambda *a, **k: pytest.fail("claude ran"))
    with pytest.raises(dispatch.DispatchError, match="--advisor"):
        dispatch.spawn_child("x", cwd=tmp_path, model=model, advisor=advisor)


@pytest.mark.real_spawn
def test_grok_has_no_advisor(tmp_path: Path) -> None:
    with pytest.raises(dispatch.DispatchError, match="Grok children have no advisor"):
        dispatch.spawn_child("x", cwd=tmp_path, agent="grok", advisor="opus")
    with pytest.raises(dispatch.DispatchError, match="Grok children have no advisor"):
        dispatch.check_grok(advisor="opus")


# --- the flag reaches every child -------------------------------------------


def _build_repo(parent: Path) -> None:
    root = parent / "proj"
    root.mkdir()
    for args in (["init", "-q", "-b", "master"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    _git_template.copy("dispatch-advisor-repo", _build_repo, tmp_path, tmp_path_factory)
    return tmp_path / "proj"


def _fetch(issue: int, *, repo_root):
    return dispatch.Issue(number=issue, title="t", body="b")


class Spawned:
    """What the spawn chokepoint was handed, one entry per child."""

    def __init__(self) -> None:
        self.calls: List[Dict] = []

    def __call__(self, prompt, *, cwd, model=None, lean=True, effort=None,
                 read_only=False, settings=None, advisor=None, permission_mode=None):
        self.calls.append({"prompt": prompt, "model": model, "advisor": advisor,
                           "effort": effort, "settings": settings})
        return f"{len(self.calls):08x}"


@pytest.fixture
def spawned(monkeypatch) -> Spawned:
    rec = Spawned()
    monkeypatch.setattr(dispatch, "spawn_child", rec)
    monkeypatch.setattr(claude_cli, "verify_registered", lambda *a, **k: None)
    return rec


def test_every_child_of_one_dispatch_gets_the_advisor(repo: Path, spawned) -> None:
    results = dispatch.dispatch_all([1, 2], repo_root=repo, fetch=_fetch,
                                    model="sonnet", advisor="opus")
    assert [c["advisor"] for c in spawned.calls] == ["opus", "opus"]
    assert [r.advisor for r in results] == ["opus", "opus"]
    assert [r.model for r in results] == ["sonnet", "sonnet"]


def test_a_dispatch_with_no_advisor_records_none(repo: Path, spawned) -> None:
    result = dispatch.dispatch_issue(1, repo_root=repo, fetch=_fetch)
    assert spawned.calls[0]["advisor"] is None and result.advisor is None


def test_a_batch_whose_advisor_would_be_dropped_starts_nothing(repo: Path, spawned) -> None:
    fetched: list = []
    results = dispatch.dispatch_all(
        [1, 2], repo_root=repo, model="opus", advisor="sonnet",
        fetch=lambda n, *, repo_root: fetched.append(n),
    )
    assert spawned.calls == [] and fetched == []
    assert all("ranks below" in r.error for r in results)
    assert not dispatch.worktree_path(1, repo_root=repo).exists()


def _contract(repo: Path, pieces: list) -> contracts.Contract:
    return contracts.Contract(
        feature="demo", verdict="parallel", path=repo / "c.md",
        pieces=[contracts.Piece(slug=f"p{i}", files=[f"{i}.py"], exposes=["`f()`"], **kw)
                for i, kw in enumerate(pieces)],
    )


def test_a_pieces_own_advisor_wins_over_the_flag(repo: Path, spawned) -> None:
    dispatch.dispatch_contract(
        _contract(repo, [{"advisor": "fable"}, {}, {"model": "haiku"}]),
        repo_root=repo, model="sonnet", advisor="opus",
    )
    assert [(c["model"], c["advisor"]) for c in spawned.calls] == [
        ("sonnet", "fable"), ("sonnet", "opus"), ("haiku", "opus")]


def test_one_inverted_piece_refuses_the_whole_contract(repo: Path, spawned) -> None:
    with pytest.raises(dispatch.DispatchError, match="p1: advisor: sonnet ranks below"):
        dispatch.dispatch_contract(
            _contract(repo, [{}, {"model": "opus", "advisor": "sonnet"}]),
            repo_root=repo, model="haiku",
        )
    assert spawned.calls == []
    assert not dispatch.worktree_path("c-p0", repo_root=repo).exists()


# --- parsing `advisor:` out of a contract ------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "c.md"
    path.write_text(
        "---\nfeature: demo\ncreated: 2026-01-01\nverdict: parallel\n---\n\n" + body,
        encoding="utf-8",
    )
    return path


def test_a_piece_may_name_its_advisor_and_it_does_not_leak(tmp_path: Path) -> None:
    path = _write(tmp_path, "## one\n\n- **files:** a.py\n- **advisor:** opus\n\n"
                            "## two\n\n- **files:** b.py\n\n"
                            "## three\n\n- **files:** c.py\n- **advisor:** none\n")
    assert [p.advisor for p in contracts.parse_contract(path).pieces] == ["opus", None, None]


@pytest.mark.parametrize("value", ["opus, or fable if stuck", "opus;", "`opus` please"])
def test_an_advisor_that_is_not_one_token_is_refused(tmp_path: Path, value: str) -> None:
    path = _write(tmp_path, f"## one\n\n- **files:** a.py\n- **advisor:** {value}\n")
    with pytest.raises(contracts.ContractError, match="advisor"):
        contracts.parse_contract(path)


def test_the_example_documents_the_field_and_still_parses(tmp_path: Path) -> None:
    assert "advisor:" in contracts.EXAMPLE
    path = tmp_path / "c.md"
    path.write_text(contracts.EXAMPLE, encoding="utf-8")
    # Documented, not recommended: no example piece names one (#818's bar).
    assert [p.advisor for p in contracts.parse_contract(path).pieces] == [None, None]


# --- reading it back off state.json -------------------------------------------


def test_the_advisor_is_read_from_respawn_flags() -> None:
    assert advisor_from({"respawnFlags": LEAN_ADVISED_FLAGS}) == "opus"
    assert advisor_from({"respawnFlags": ["--model", "opus"]}) is None
    assert advisor_from({}) is None


def test_read_sessions_and_sessions_json_carry_it(tmp_path: Path, monkeypatch, capsys) -> None:
    for short_id, flags in (("a1b2c3d4", LEAN_ADVISED_FLAGS), ("b1b2c3d4", [])):
        entry = tmp_path / short_id
        entry.mkdir()
        (entry / "state.json").write_text(json.dumps({
            "state": "done", "tempo": "idle", "cwd": str(tmp_path), "respawnFlags": flags,
        }), encoding="utf-8")
    found = read_sessions(tmp_path)
    assert {s.short_id: s.advisor for s in found} == {"a1b2c3d4": "opus", "b1b2c3d4": None}

    from mnemo.cli.commands import sessions as sessions_cmd

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda root=None, *, cwd=None: found)
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep",
                        lambda sessions, *, vault_root: 0)
    assert sessions_cmd.cmd_sessions(argparse.Namespace(json=True, watch=False,
                                                        **{"all": True})) == 0
    rows = {r["short_id"]: r["advisor"] for r in json.loads(capsys.readouterr().out)}
    assert rows == {"a1b2c3d4": "opus", "b1b2c3d4": None}


# --- the command line -----------------------------------------------------------


def _args(**kw):
    return argparse.Namespace(**{"issues": [818], "dry_run": False, "contract": None,
                                 "model": None, "effort": None, **kw})


def test_the_advisor_flag_is_on_the_parser() -> None:
    from mnemo.cli.parser import _build_parser

    assert _build_parser().parse_args(["dispatch", "1", "--advisor", "opus"]).advisor == "opus"
    assert _build_parser().parse_args(["dispatch", "1"]).advisor is None


@pytest.mark.parametrize(("kw", "words"), [
    ({"model": "opus", "advisor": "sonnet"}, "ranks below the child's model opus"),
    ({"advisor": "opus please"}, "not a model id"),
    ({"advisor": "opus", "agent": "grok"}, "Grok children have no advisor"),
])
def test_the_command_refuses_before_anything_spawns(monkeypatch, capsys, tmp_path, kw, words):
    from mnemo.cli.commands import dispatch as dispatch_cmd

    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dispatch, "dispatch_all", lambda *a, **k: pytest.fail("dispatched"))
    assert dispatch_cmd.cmd_dispatch(_args(**kw)) == 1
    assert words in capsys.readouterr().out


def test_the_flag_reaches_the_dispatch_and_the_report(monkeypatch, capsys, tmp_path) -> None:
    from mnemo.cli.commands import dispatch as dispatch_cmd

    seen: dict = {}

    def fake_all(issues, *, repo_root, model=None, lean=True, may=(), effort=None,
                 read_only=False, advisor=None):
        seen["advisor"] = advisor
        return [dispatch.Dispatched(issue=818, worktree=tmp_path, short_id="a1b2c3d4",
                                    model=model, advisor=advisor)]

    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dispatch, "dispatch_all", fake_all)
    assert dispatch_cmd.cmd_dispatch(_args(model="sonnet", advisor="opus")) == 0
    assert seen["advisor"] == "opus"
    assert "[sonnet, advisor opus]" in capsys.readouterr().out


def test_a_dry_run_shows_each_pieces_advisor_and_refuses_an_inverted_one(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    from mnemo.cli.commands import dispatch as dispatch_cmd

    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dispatch, "spawn_child", lambda *a, **k: pytest.fail("spawned"))
    path = _write(tmp_path, "## one\n\n- **files:** a.py\n- **advisor:** fable\n\n"
                            "## two\n\n- **files:** b.py\n")
    assert dispatch_cmd.cmd_dispatch(_args(issues=[], contract=str(path), dry_run=True,
                                           model="sonnet", advisor="opus")) == 0
    lines = [l.split("  may: ")[0] for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert lines[0].endswith("[sonnet, advisor fable]") and lines[1].endswith(
        "[sonnet, advisor opus]")

    assert dispatch_cmd.cmd_dispatch(_args(issues=[], contract=str(path), dry_run=True,
                                           model="opus", advisor="sonnet")) == 1
    assert "two: --advisor: sonnet ranks below" in capsys.readouterr().out


def test_mnemo_session_names_the_advisor(monkeypatch, capsys) -> None:
    from mnemo.cli.commands import session as session_cmd

    found = Session(short_id="a1b2c3d4", state="done", tempo="idle", model="sonnet",
                    advisor="opus")
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", lambda **kw: [found])
    session_cmd.cmd_session(argparse.Namespace(short_id="a1b2c3d4", limit=5))
    assert "sonnet · advisor opus" in capsys.readouterr().out


# --- twins as two arms (#818's measurement) -------------------------------------


def _tags(*tags: str):
    it = iter(tags)
    return lambda: next(it)


class _Keep:
    """An rng whose shuffle keeps the order: advised arm first."""

    def shuffle(self, items):
        return None


class _Swap:
    def shuffle(self, items):
        items.reverse()


def test_twins_with_an_advisor_are_two_arms_on_one_issue_prompt(repo, tmp_path, spawned) -> None:
    vault = tmp_path / "vault"
    pair_id, results = twins.dispatch_twins(
        818, repo_root=repo, fetch=_fetch, vault_root=vault, draw=_tags("aaaaaa", "bbbbbb"),
        model="sonnet", advisor="opus", effort="high", rng=_Keep(),
    )
    a, b = spawned.calls
    assert (a["model"], a["advisor"]) == ("sonnet", "opus")
    assert (b["model"], b["advisor"]) == ("opus", None)
    # One issue prompt; the advised arm's also says when to consult (#832).
    assert a["prompt"] == dispatch.build_prompt(818, title="t", body="b", repo_root=repo,
                                                blind=True, advised=True)
    assert b["prompt"] == dispatch.build_prompt(818, title="t", body="b", repo_root=repo,
                                                blind=True)
    assert a["effort"] == b["effort"] == "high"
    assert a["settings"] == b["settings"] == twins.SETTINGS
    assert [(r.model, r.advisor) for r in results] == [("sonnet", "opus"), ("opus", None)]

    pair = twins.read_pairs(vault)[pair_id]
    assert pair.has_arms and pair.advisor == "opus" and pair.model == "sonnet"
    assert pair.arm_of("aaaaaa") == ("sonnet", "opus")
    assert pair.arm_of("bbbbbb") == ("opus", None)


def test_which_tag_is_advised_is_drawn(repo, tmp_path, spawned) -> None:
    vault = tmp_path / "vault"
    pair_id, _ = twins.dispatch_twins(
        818, repo_root=repo, fetch=_fetch, vault_root=vault, draw=_tags("aaaaaa", "bbbbbb"),
        model="sonnet", advisor="opus", rng=_Swap(),
    )
    assert [c["advisor"] for c in spawned.calls] == [None, "opus"]
    assert twins.read_pairs(vault)[pair_id].arm_of("bbbbbb") == ("sonnet", "opus")


def test_twins_without_an_advisor_write_the_pair_line_they_always_did(
    repo, tmp_path, spawned
) -> None:
    vault = tmp_path / "vault"
    pair_id, _ = twins.dispatch_twins(818, repo_root=repo, fetch=_fetch, vault_root=vault,
                                      draw=_tags("aaaaaa", "bbbbbb"), model="opus")
    line = json.loads(twins.log_path(vault).read_text(encoding="utf-8").splitlines()[0])
    assert "advisor" not in line
    assert all(set(t) == {"tag", "tree", "branch"} for t in line["twins"])
    assert spawned.calls[0]["prompt"] == spawned.calls[1]["prompt"]
    pair = twins.read_pairs(vault)[pair_id]
    assert not pair.has_arms and pair.arm_of("aaaaaa") == ("opus", None)
    assert [c["advisor"] for c in spawned.calls] == [None, None]


@pytest.mark.parametrize(("model", "words"), [
    (None, "name the advised twin's --model"),
    ("fable", "ranks below"),
])
def test_an_arms_pair_is_refused_before_anything_exists(repo, tmp_path, spawned, model, words):
    vault = tmp_path / "vault"
    with pytest.raises(dispatch.DispatchError, match=words):
        twins.dispatch_twins(818, repo_root=repo, vault_root=vault,
                             fetch=lambda *a, **k: pytest.fail("fetched"),
                             model=model, advisor="opus")
    assert spawned.calls == [] and not twins.log_path(vault).exists()


def test_the_twins_dry_run_names_both_arms(monkeypatch, capsys, tmp_path) -> None:
    from mnemo.cli.commands import dispatch as dispatch_cmd

    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: tmp_path)
    assert dispatch_cmd.cmd_dispatch(_args(twins=True, dry_run=True, model="sonnet",
                                           advisor="opus")) == 0
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert lines[0].endswith("[sonnet, advisor opus]") and lines[1].endswith("[opus]")

    assert dispatch_cmd.cmd_dispatch(_args(twins=True, dry_run=True, advisor="opus")) == 1
    assert "--model" in capsys.readouterr().out


def test_the_reveal_names_each_arm(tmp_path, capsys) -> None:
    from mnemo.cli.commands import twins as twins_cmd

    vault = tmp_path / "v"
    twins._append(vault, {
        "event": "pair", "pair": "abcdef", "issue": 7, "repo_root": "/r", "base": "0" * 40,
        "model": "sonnet", "advisor": "opus",
        "twins": [{"tag": "aaaaaa", "tree": "/a", "branch": "a", "model": "sonnet",
                   "advisor": "opus"},
                  {"tag": "bbbbbb", "tree": "/b", "branch": "b", "model": "opus",
                   "advisor": None}],
    })
    for tag in ("aaaaaa", "bbbbbb"):
        twins._append(vault, {"event": "started", "pair": "abcdef", "tag": tag,
                              "short_id": "0000000" + tag[0]})
    twins._append(vault, {"event": "shown", "pair": "abcdef", "order": ["bbbbbb", "aaaaaa"]})
    twins._append(vault, {"event": "prefer", "pair": "abcdef", "choice": "tie"})
    twins_cmd._reveal(twins.read_pairs(vault)["abcdef"])
    out = capsys.readouterr().out
    assert "A was 0000000b  /b  [opus]" in out
    assert "B was 0000000a  /a  [sonnet, advisor opus]" in out


def test_the_run_to_run_readout_leaves_arms_pairs_out(tmp_path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools import measure_child_pairs

    vault = tmp_path / "v"
    twins._append(vault, {
        "event": "pair", "pair": "abcdef", "issue": 7, "repo_root": "/r", "base": "0" * 40,
        "model": "sonnet", "advisor": "opus",
        "twins": [{"tag": "aaaaaa", "tree": "/a", "branch": "a", "model": "sonnet",
                   "advisor": "opus"},
                  {"tag": "bbbbbb", "tree": "/b", "branch": "b", "model": "opus"}],
    })
    for tag in ("aaaaaa", "bbbbbb"):
        twins._append(vault, {"event": "started", "pair": "abcdef", "tag": tag,
                              "short_id": "0000000" + tag[0]})
    pairs = list(twins.read_pairs(vault).values())
    assert measure_child_pairs.rows(pairs, live=lambda tag: pytest.fail("read")) == []
