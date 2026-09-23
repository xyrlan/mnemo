"""``mnemo dispatch <n> --twins`` and ``mnemo twins`` — one issue run twice, read blind (#449).

Real git repos, because the base commit and the two branches are the claims
being tested and a stubbed git would test the stub. The spawn is stubbed at
``dispatch.spawn_child``, the chokepoint the suite already owns (#408/#428):
nothing real starts, and each test pins what the stub was *given*.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
from pathlib import Path
from typing import Dict, List

import pytest

from mnemo.cli.commands import deliver, dispatch as dispatch_cmd, twins as twins_cmd
from mnemo.core import claude_cli, dispatch, twins
from mnemo.core.sessions import delivery, grants, pr_follow
from mnemo.hooks import session_end


def _run(args, *, cwd) -> str:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                          check=True).stdout


def _commit(root: Path, name: str, text: str) -> str:
    (root / name).write_text(text, encoding="utf-8")
    _run(["git", "add", name], cwd=root)
    _run(["git", "commit", "-m", name], cwd=root)
    return _run(["git", "rev-parse", "HEAD"], cwd=root).strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "mnemo"
    root.mkdir()
    _run(["git", "init", "-b", "master"], cwd=root)
    _run(["git", "config", "user.email", "t@example.com"], cwd=root)
    _run(["git", "config", "user.name", "t"], cwd=root)
    _commit(root, "README.md", "base\n")
    (root / "changelog.d").mkdir()
    return root


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


def _issue(number, *, repo_root):
    return dispatch.Issue(number=number, title="measure the thing",
                          body="The body, verbatim.")


class Spawned:
    """What the spawn chokepoint was handed, one entry per child."""

    def __init__(self) -> None:
        self.calls: List[Dict] = []

    def __call__(self, prompt, *, cwd, model=None, lean=True, effort=None,
                 read_only=False, settings=None):
        self.calls.append({"prompt": prompt, "cwd": Path(cwd), "model": model,
                           "lean": lean, "effort": effort, "read_only": read_only,
                           "settings": settings})
        return f"{len(self.calls):08x}"


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> Spawned:
    rec = Spawned()
    monkeypatch.setattr(dispatch, "spawn_child", rec)
    monkeypatch.setattr(claude_cli, "verify_registered", lambda short_id, *, cwd: None)
    return rec


def _tags(*tags: str):
    it = iter(tags)
    return lambda: next(it)


def _dispatch(repo: Path, vault: Path, **kw):
    return twins.dispatch_twins(
        449, repo_root=repo, fetch=_issue, vault_root=vault,
        draw=kw.pop("draw", _tags("aaaaaa", "bbbbbb")), **kw,
    )


# --- naming: the new shape, and the old ones unchanged ---------------------


@pytest.mark.parametrize(("cwd", "target", "tag"), [
    ("/x/mnemo-wt-449-3fa9c1", 449, "3fa9c1"),
    ("/x/mnemo-wt-449-3fa9c1/", 449, "3fa9c1"),
    ("/x/mnemo-wt-449", 449, None),
    ("/x/mnemo-wt-c-parser", "c-parser", None),
    ("/x/mnemo-wt-12-old", None, None),       # hand-made: not six hex digits
    ("/x/mnemo-wt-12-abcdeg", None, None),    # `g` is not hex
    ("/x/mnemo-wt-feature", None, None),
])
def test_a_twin_tree_reads_back_as_its_issue_and_nothing_else_moves(cwd, target, tag):
    assert dispatch.issue_for_cwd(cwd) == target
    assert dispatch.twin_tag_for_cwd(cwd) == tag


def test_a_twin_is_named_beside_the_repo_on_its_own_branch() -> None:
    assert dispatch.worktree_path(449, repo_root="/x/mnemo", tag="3fa9c1") == \
        Path("/x/mnemo-wt-449-3fa9c1")
    assert dispatch.branch_name(449, tag="3fa9c1") == "fix/issue-449-3fa9c1"
    # Dispatching from inside a twin's tree does not stack the suffix.
    assert dispatch.worktree_path(450, repo_root="/x/mnemo-wt-449-3fa9c1") == \
        Path("/x/mnemo-wt-450")


@pytest.mark.parametrize("tag", ["a", "ABCDEF", "abcdefg", "zzzzzz"])
def test_a_tag_the_parser_would_not_read_back_is_refused(tag) -> None:
    with pytest.raises(ValueError):
        dispatch.worktree_path(449, repo_root="/x/mnemo", tag=tag)
    with pytest.raises(ValueError):
        dispatch.branch_name(449, tag=tag)


def test_a_drawn_tag_is_random_hex_not_an_ordinal() -> None:
    drawn = {dispatch.new_twin_tag() for _ in range(20)}
    assert len(drawn) > 1
    assert all(dispatch.twin_tag_for_cwd(f"/x/m-wt-1-{t}") == t for t in drawn)


# --- the prompt: shared byte for byte, and the plain one unchanged ---------


def test_the_blind_prompt_names_no_branch_and_differs_in_that_sentence_only() -> None:
    plain = dispatch.build_prompt(449, title="t", body="b")
    blind = dispatch.build_prompt(449, title="t", body="b", blind=True)

    assert "on branch `fix/issue-449`. Work only here." in plain
    assert "fix/issue" not in blind
    assert "on a branch of its own. Work only here." in blind
    assert blind.replace("on a branch of its own", "on branch `fix/issue-449`") == plain
    for word in ("twin", "pair", "another run"):
        assert word not in blind.lower()


# --- dispatch --------------------------------------------------------------


def test_two_twins_get_one_prompt_one_base_and_the_same_settings(
    repo: Path, vault: Path, spawned: Spawned,
) -> None:
    base = _run(["git", "rev-parse", "HEAD"], cwd=repo).strip()

    pair_id, results = _dispatch(repo, vault, model="haiku", effort="high")

    assert [r.error for r in results] == [None, None]
    a, b = spawned.calls
    assert a["prompt"] == b["prompt"]                      # byte for byte
    assert (a["model"], a["effort"], a["lean"]) == (b["model"], b["effort"], b["lean"]) \
        == ("haiku", "high", True)
    assert a["cwd"] != b["cwd"]
    for call, tag in zip(spawned.calls, ("aaaaaa", "bbbbbb")):
        assert call["cwd"] == repo.parent / f"mnemo-wt-449-{tag}"
        assert _run(["git", "rev-parse", "HEAD"], cwd=call["cwd"]).strip() == base
        assert _run(["git", "branch", "--show-current"], cwd=call["cwd"]).strip() == \
            f"fix/issue-449-{tag}"

    pair = twins.read_pairs(vault)[pair_id]
    assert pair.base == base
    assert [t.short_id for t in pair.twins] == ["00000001", "00000002"]
    import hashlib
    assert pair.prompt_sha256 == hashlib.sha256(a["prompt"].encode()).hexdigest()


def test_the_second_twin_branches_from_the_first_ones_commit_even_if_head_moves(
    repo: Path, vault: Path, monkeypatch: pytest.MonkeyPatch, spawned: Spawned,
) -> None:
    """Something landing between the two spawns must not split the base."""
    base = _run(["git", "rev-parse", "HEAD"], cwd=repo).strip()

    def spawn_and_land(prompt, **kw):
        _commit(repo, f"landed{len(spawned.calls)}.txt", "x\n")
        return spawned(prompt, **kw)

    monkeypatch.setattr(dispatch, "spawn_child", spawn_and_land)
    _dispatch(repo, vault)

    heads = {_run(["git", "rev-parse", "HEAD"], cwd=c["cwd"]).strip() for c in spawned.calls}
    assert heads == {base}


def test_twins_are_granted_nothing_and_so_are_never_followed(
    repo: Path, vault: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#436 wakes only children granted push; a twin must stay outside it."""
    _pair, results = _dispatch(repo, vault)

    assert all(r.may == () for r in results)
    assert "Do not merge or push without asking." in spawned.calls[0]["prompt"]
    assert grants.read(twins.default_vault()) == {}

    from mnemo.core.sessions import parents
    monkeypatch.setattr(parents, "read", lambda v: {r.short_id: "parent" for r in results})
    spawn = []
    decided = pr_follow.on_session_end(
        {}, vault_root=twins.default_vault(),
        session_id=results[0].short_id + "-1111-2222-3333-444444444444",
        cwd=str(results[0].worktree), spawn=lambda: spawn.append(1),
    )
    assert decided == "no-push" and spawn == []


def test_a_second_twin_that_fails_leaves_the_first_running_and_says_so(
    repo: Path, vault: Path, monkeypatch: pytest.MonkeyPatch, spawned: Spawned,
) -> None:
    def second_fails(prompt, **kw):
        if spawned.calls:
            raise dispatch.DispatchError("claude --bg failed: boom")
        return spawned(prompt, **kw)

    monkeypatch.setattr(dispatch, "spawn_child", second_fails)
    pair_id, results = _dispatch(repo, vault)

    assert results[0].error is None and "boom" in results[1].error
    assert not (repo.parent / "mnemo-wt-449-bbbbbb").exists()   # rolled back
    pair = twins.read_pairs(vault)[pair_id]
    assert len(pair.started) == 1
    with pytest.raises(twins.TwinsError, match="nothing to compare"):
        twins.show(vault, pair_id)


# --- blind reading ---------------------------------------------------------


def _work(tree: Path, text: str) -> None:
    (tree / "fix.py").write_text(text, encoding="utf-8")
    _run(["git", "add", "fix.py"], cwd=tree)
    _run(["git", "commit", "-m", "fix"], cwd=tree)


def _done(_short_id):
    return {"state": "done", "tokens": 1000, "createdAt": "2026-09-22T10:00:00Z",
            "firstTerminalAt": "2026-09-22T10:14:00Z"}


@pytest.fixture
def pair(repo: Path, vault: Path, spawned: Spawned):
    pair_id, results = _dispatch(repo, vault)
    _work(results[0].worktree, "first = 'mnemo-wt-449-aaaaaa'\n")
    _work(results[1].worktree, "second = 2\n")
    return pair_id, results


def test_show_refuses_while_a_twin_is_still_working(vault: Path, pair) -> None:
    pair_id, _ = pair
    with pytest.raises(twins.TwinsError, match="still working"):
        twins.show(vault, pair_id, state=lambda sid: {"state": "working"})
    assert twins.read_pairs(vault)[pair_id].order == ()


def test_show_labels_only_A_and_B_in_a_drawn_order_that_then_holds(vault: Path, pair) -> None:
    pair_id, _ = pair
    rng = random.Random(0)

    first = twins.show(vault, pair_id, rng=rng, state=_done)
    recorded = twins.read_pairs(vault)[pair_id].order
    again = twins.show(vault, pair_id, rng=random.Random(99), state=_done)

    assert first == again                                   # the order is drawn once
    assert sorted(recorded) == ["aaaaaa", "bbbbbb"]
    for name in ("aaaaaa", "bbbbbb", "00000001", "00000002", "fix/issue-449-"):
        assert name not in first
    assert "'<twin>'" in first                              # a self-signed file is scrubbed
    assert "===== A =====" in first and "===== B =====" in first
    # A is whichever twin the draw put first.
    a_text = first.split("===== A =====")[1].split("===== B =====")[0]
    assert ("first" in a_text) == (recorded[0] == "aaaaaa")


def test_the_order_is_really_drawn(vault: Path, repo: Path, spawned: Spawned) -> None:
    firsts = set()
    for seed in range(12):
        pair_id, results = twins.dispatch_twins(
            449, repo_root=repo, fetch=_issue, vault_root=vault,
        )
        for r in results:
            _work(r.worktree, "x = 1\n")
        twins.show(vault, pair_id, rng=random.Random(seed), state=_done)
        firsts.add(twins.read_pairs(vault)[pair_id].order[0]
                   == twins.read_pairs(vault)[pair_id].twins[0].tag)
    assert firsts == {True, False}


def test_show_snapshots_both_twins_tokens_and_wall_time(vault: Path, pair) -> None:
    pair_id, _ = pair
    states = {"00000001": {"state": "done", "tokens": 30000,
                           "createdAt": "2026-09-22T10:00:00.000Z",
                           "firstTerminalAt": "2026-09-22T10:10:00.000Z"},
              "00000002": {"state": "stopped", "tokens": 45000,
                           "createdAt": "2026-09-22T10:00:05Z",
                           "lastTerminalAt": "2026-09-22T10:20:05Z"}}
    twins.show(vault, pair_id, state=states.get)

    metrics = twins.read_pairs(vault)[pair_id].metrics
    assert metrics == {"aaaaaa": {"tokens": 30000, "wall_seconds": 600.0},
                       "bbbbbb": {"tokens": 45000, "wall_seconds": 1200.0}}


def test_an_answer_needs_the_blind_read_first_and_is_given_once(vault: Path, pair) -> None:
    pair_id, _ = pair
    with pytest.raises(twins.TwinsError, match="not been shown"):
        twins.prefer(vault, pair_id, "A")

    twins.show(vault, pair_id, rng=random.Random(1), state=_done)
    answered = twins.prefer(vault, pair_id, "b")
    assert answered.choice == "B" and answered.preferred == answered.order[1]

    with pytest.raises(twins.TwinsError, match="already answered"):
        twins.prefer(vault, pair_id, "A")
    with pytest.raises(twins.TwinsError, match="say A, B or tie"):
        twins.prefer(vault, pair_id, "C")
    # A hand-appended second answer is ignored by the reader too.
    twins._append(vault, {"event": "prefer", "pair": pair_id, "choice": "A"})
    assert twins.read_pairs(vault)[pair_id].choice == "B"


def test_a_tie_names_no_twin(vault: Path, pair) -> None:
    pair_id, _ = pair
    twins.show(vault, pair_id, state=_done)
    answered = twins.prefer(vault, pair_id, "TIE")
    assert (answered.choice, answered.preferred) == ("tie", "")


def test_a_pair_is_found_by_its_issue_only_when_that_is_unambiguous(
    vault: Path, repo: Path, spawned: Spawned,
) -> None:
    first, _ = _dispatch(repo, vault)
    assert twins.resolve(vault, "#449").pair == first
    _dispatch(repo, vault, draw=_tags("cccccc", "dddddd"))
    with pytest.raises(twins.TwinsError, match="2 pairs"):
        twins.resolve(vault, "449")


def test_the_cli_reveals_the_labels_only_after_the_answer(
    vault: Path, pair, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    pair_id, results = pair
    monkeypatch.setattr(twins, "default_vault", lambda: vault)
    monkeypatch.setattr(twins, "_state", _done)

    ns = lambda **kw: argparse.Namespace(**{"action": "list", "pair": None, "answer": None, **kw})
    assert twins_cmd.cmd_twins(ns()) == 0
    assert "ready to judge" in capsys.readouterr().out

    assert twins_cmd.cmd_twins(ns(action="show", pair=pair_id)) == 0
    shown = capsys.readouterr().out
    assert "00000001" not in shown and "00000002" not in shown

    assert twins_cmd.cmd_twins(ns(action="prefer", pair=pair_id, answer="A")) == 0
    out = capsys.readouterr().out
    order = twins.read_pairs(vault)[pair_id].order
    winner = "00000001" if order[0] == "aaaaaa" else "00000002"
    assert f"A was {winner}" in out
    assert f"mnemo deliver {winner}" in out


# --- delivery --------------------------------------------------------------


@pytest.fixture
def delivering(repo: Path, vault: Path, monkeypatch: pytest.MonkeyPatch):
    """Deliver in *repo* with nothing reaching GitHub; returns the call log."""
    calls: list = []
    monkeypatch.setattr(deliver, "_repo_root", lambda: repo)
    monkeypatch.setattr(twins, "default_vault", lambda: vault)
    monkeypatch.setattr(delivery, "pr_info", lambda branch, **kw: None)
    monkeypatch.setattr(delivery, "sessions_in", lambda tree: [])
    monkeypatch.setattr(delivery, "push",
                        lambda branch, *, worktree: calls.append(("push", branch)))
    monkeypatch.setattr(
        delivery, "open_pr",
        lambda branch, *, worktree, title, target=None: calls.append(("pr", branch, target))
        or "https://x/pull/9",
    )
    return calls


def _deliver(*ids: str) -> int:
    return deliver.cmd_deliver(argparse.Namespace(ids=list(ids), review=False))


def test_a_twin_is_not_delivered_before_the_blind_read(
    vault: Path, pair, delivering: list, capsys,
) -> None:
    pair_id, results = pair
    assert _deliver("#449-aaaaaa") == 1
    assert delivering == []
    assert f"mnemo twins show {pair_id}" in capsys.readouterr().out


def test_naming_the_issue_names_neither_twin(vault: Path, pair, delivering: list, capsys) -> None:
    pair_id, _ = pair
    twins.show(vault, pair_id, state=_done)
    twins.prefer(vault, pair_id, "A")

    assert _deliver("449") == 1
    assert delivering == []
    assert "2 dispatch worktrees" in capsys.readouterr().out


def test_the_preferred_twin_delivers_once_and_its_pair_only_once(
    vault: Path, pair, delivering: list, capsys,
) -> None:
    pair_id, _ = pair
    twins.show(vault, pair_id, state=_done)
    twins.prefer(vault, pair_id, "A")
    chosen = twins.read_pairs(vault)[pair_id].order[0]
    other = "bbbbbb" if chosen == "aaaaaa" else "aaaaaa"

    assert _deliver(f"#449-{chosen}") == 0
    assert delivering == [("push", f"fix/issue-449-{chosen}"),
                          ("pr", f"fix/issue-449-{chosen}", 449)]
    assert twins.read_pairs(vault)[pair_id].delivered == chosen

    assert _deliver(f"#449-{other}") == 1
    assert "one issue, one PR" in capsys.readouterr().out
    assert len(delivering) == 2


def test_a_plain_child_of_the_same_issue_is_untouched_by_the_gate(
    repo: Path, vault: Path, delivering: list,
) -> None:
    tree = dispatch.ensure_worktree(311, repo_root=repo)
    _work(tree, "x = 1\n")
    assert _deliver("311") == 0
    assert delivering == [("push", "fix/issue-311"), ("pr", "fix/issue-311", 311)]


# --- the held briefing -----------------------------------------------------


def _session_end(vault: Path, cwd: Path, tmp_path: Path, monkeypatch) -> list:
    """Run SessionEnd's briefing step for a session in *cwd*; return what it spawned."""
    spawned: list = []
    transcript = tmp_path / f"{cwd.name}.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(session_end, "_spawn_detached_briefing",
                        lambda jsonl, agent: spawned.append((str(jsonl), agent)))
    monkeypatch.setattr(session_end, "_resolve_session_jsonl_path",
                        lambda sid, c: transcript)
    session_end._maybe_schedule_briefing(
        {"briefings": {"enabled": True}}, vault, "mnemo",
        session_id="00000001-aaaa", cwd=str(cwd),
    )
    return spawned


def test_a_twin_ending_undelivered_teaches_nothing_and_its_release_is_the_delivery(
    vault: Path, pair, delivering: list, tmp_path: Path, monkeypatch,
) -> None:
    pair_id, results = pair

    for r in results:
        assert _session_end(vault, r.worktree, tmp_path, monkeypatch) == []
    assert set(twins.read_pairs(vault)[pair_id].held) == {"aaaaaa", "bbbbbb"}

    twins.show(vault, pair_id, state=_done)
    twins.prefer(vault, pair_id, "B")
    chosen = twins.read_pairs(vault)[pair_id].order[1]
    released: list = []
    monkeypatch.setattr(session_end, "_spawn_detached_briefing",
                        lambda jsonl, agent: released.append((Path(jsonl).name, agent)))

    assert _deliver(f"#449-{chosen}") == 0

    assert released == [(f"mnemo-wt-449-{chosen}.jsonl", "mnemo")]
    assert twins.read_pairs(vault)[pair_id].released == (chosen,)


def test_a_twin_still_running_when_delivered_is_briefed_by_its_own_session_end(
    vault: Path, pair, delivering: list, tmp_path: Path, monkeypatch,
) -> None:
    pair_id, _ = pair
    twins.show(vault, pair_id, state=_done)
    twins.prefer(vault, pair_id, "A")
    chosen = twins.read_pairs(vault)[pair_id].order[0]
    other = "bbbbbb" if chosen == "aaaaaa" else "aaaaaa"
    assert _deliver(f"#449-{chosen}") == 0

    tree = lambda tag: Path(twins.read_pairs(vault)[pair_id].twin(tag).tree)
    assert _session_end(vault, tree(chosen), tmp_path, monkeypatch) != []
    assert _session_end(vault, tree(other), tmp_path, monkeypatch) == []


def test_a_twin_with_no_record_still_holds(vault: Path, tmp_path: Path, monkeypatch) -> None:
    """A lost log fails closed: nothing learned, nothing written."""
    cwd = tmp_path / "mnemo-wt-7-abcdef"
    cwd.mkdir()
    assert _session_end(vault, cwd, tmp_path, monkeypatch) == []


def test_an_ordinary_child_is_briefed_as_before(vault: Path, tmp_path: Path, monkeypatch) -> None:
    cwd = tmp_path / "mnemo-wt-7"
    cwd.mkdir()
    assert _session_end(vault, cwd, tmp_path, monkeypatch) != []


def test_a_held_twin_is_never_analyzed_by_the_proposer(
    vault: Path, tmp_path: Path, monkeypatch,
) -> None:
    import mnemo.autopilot.core.kill_switch as kill_switch
    import mnemo.autopilot.proposer.eos_extractor as eos
    from mnemo.core import session as session_mod

    analyzed, marked = [], []
    monkeypatch.setattr(kill_switch, "is_active", lambda **kw: True)
    monkeypatch.setattr(eos, "analyze_session", lambda **kw: analyzed.append(kw["cwd"]))
    monkeypatch.setattr(session_mod, "mark_analyzed", lambda sid: marked.append(sid))

    for name in ("mnemo-wt-7-abcdef", "mnemo-wt-7"):
        session_end._maybe_schedule_propose(
            {}, vault, "mnemo", session_id=name, cwd=str(tmp_path / name),
        )
    assert analyzed == [tmp_path / "mnemo-wt-7"]
    assert marked == ["mnemo-wt-7-abcdef", "mnemo-wt-7"]


# --- the command line ------------------------------------------------------


def _dispatch_args(**kw) -> argparse.Namespace:
    base = {"issues": [449], "contract": None, "model": None, "effort": None,
            "may": None, "read_only": False, "example": False, "dry_run": False,
            "full_profile": False, "twins": True}
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.parametrize(("kw", "said"), [
    ({"issues": [449, 450]}, "exactly one issue"),
    ({"issues": [], "contract": "plan.md"}, "not an issue"),
    ({"may": "pr"}, "--may can only be none"),
    ({"may": "push"}, "--may can only be none"),
])
def test_dispatch_twins_refuses_before_anything_spawns(
    repo: Path, monkeypatch: pytest.MonkeyPatch, spawned: Spawned, capsys, kw, said,
) -> None:
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: repo)
    assert dispatch_cmd.cmd_dispatch(_dispatch_args(**kw)) == 1
    assert said in capsys.readouterr().out
    assert spawned.calls == []


def test_dispatch_twins_runs_the_pair_and_names_it(
    repo: Path, vault: Path, monkeypatch: pytest.MonkeyPatch, spawned: Spawned, capsys,
) -> None:
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: repo)
    monkeypatch.setattr(twins, "default_vault", lambda: vault)
    # `fetch` defaults to `gh`, bound at import: hand the stub in at the call.
    real = twins.dispatch_twins
    monkeypatch.setattr(twins, "dispatch_twins",
                        lambda issue, **kw: real(issue, fetch=_issue, **kw))

    assert dispatch_cmd.cmd_dispatch(_dispatch_args(may="none")) == 0
    out = capsys.readouterr().out
    (pair_id,) = twins.read_pairs(vault)
    assert f"mnemo twins show {pair_id}" in out
    assert len(spawned.calls) == 2
    assert "may:" not in out


def test_dispatch_twins_dry_run_spawns_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch, spawned: Spawned, capsys,
) -> None:
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: repo)
    assert dispatch_cmd.cmd_dispatch(_dispatch_args(dry_run=True)) == 0
    assert capsys.readouterr().out.count("fix/issue-449-<tag>") == 2
    assert spawned.calls == []


def test_the_parser_takes_the_flag_and_the_command() -> None:
    from mnemo.cli.parser import ADVANCED_COMMANDS, _build_parser

    parser = _build_parser()
    assert parser.parse_args(["dispatch", "449", "--twins"]).twins is True
    ns = parser.parse_args(["twins", "prefer", "3fa9c1", "tie"])
    assert (ns.action, ns.pair, ns.answer) == ("prefer", "3fa9c1", "tie")
    assert parser.parse_args(["twins"]).action == "list"
    assert "twins" in ADVANCED_COMMANDS


def test_the_log_is_one_json_object_per_line(vault: Path, pair) -> None:
    lines = twins.log_path(vault).read_text(encoding="utf-8").splitlines()
    events = [json.loads(line)["event"] for line in lines]
    assert events == ["pair", "started", "started"]


# --- #453: what else reaches a twin, and its closing report ----------------
#
# Transcript records below copy the shapes read off the six pilot pairs'
# real transcripts (2026-09-22): the opening prompt is a string user turn with
# `origin.kind: human`, an answered AskUserQuestion comes back as a
# `tool_result` whose content starts "Your questions have been answered", and
# a sibling shows up inside a Bash result (`ps`, `git worktree list`).


def _line(record: Dict) -> str:
    return json.dumps(record) + "\n"


def _prompt(text="Work on issue #449 in this repo") -> str:
    return _line({"type": "user", "message": {"role": "user", "content": text},
                  "origin": {"kind": "human"}})


def _say(*blocks) -> str:
    return _line({"type": "assistant", "message": {"role": "assistant",
                                                   "content": list(blocks)}})


def _result(tool_use_id: str, content: str, **extra) -> str:
    return _line({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, **extra},
    ]}})


def _text(text: str) -> Dict:
    return {"type": "text", "text": text}


def _tool(tool_id: str, name: str, **inp) -> Dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def _transcript(tmp_path: Path, name: str, *lines: str) -> Path:
    path = tmp_path / f"{name}.jsonl"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _states(**paths: Path):
    """A `state` reader: every twin done, each with its own transcript."""
    def state(short_id):
        data = _done(short_id)
        if short_id in paths:
            data["linkScanPath"] = str(paths[short_id])
        return data
    return state


def test_twins_start_with_auto_memory_off_and_the_pair_says_so(
    repo: Path, vault: Path, spawned: Spawned,
) -> None:
    pair_id, _ = _dispatch(repo, vault)

    assert [c["settings"] for c in spawned.calls] == [{"autoMemoryEnabled": False}] * 2
    assert twins.read_pairs(vault)[pair_id].settings == {"autoMemoryEnabled": False}


def test_an_ordinary_dispatch_hands_the_chokepoint_no_settings_at_all(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not `settings=None` either: every other stub of `spawn_child` in the
    suite, and every caller, keeps the exact call it had before #453."""
    seen = []

    def strict(prompt, *, cwd, model=None, lean=True, effort=None, read_only=False):
        seen.append(cwd)
        return "0000000a"

    monkeypatch.setattr(dispatch, "spawn_child", strict)
    monkeypatch.setattr(claude_cli, "verify_registered", lambda short_id, *, cwd: None)
    result = dispatch.dispatch_issue(449, repo_root=repo, fetch=_issue)
    assert result.short_id == "0000000a" and seen


class _Run:
    """Records the argv `spawn_child` would run."""

    def __init__(self) -> None:
        self.args: List[str] = []

    def __call__(self, args, **kwargs):
        self.args = list(args)
        return subprocess.CompletedProcess(args, 0, "backgrounded · 0000000b\n", "")


def _settings_files(args: List[str]) -> List[Dict]:
    return [json.loads(Path(args[i + 1]).read_text(encoding="utf-8"))
            for i, a in enumerate(args) if a == "--settings"]


@pytest.mark.real_spawn
def test_a_lean_twin_gets_one_settings_file_holding_the_hooks_and_the_memory_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One file: given two `--settings`, the CLI keeps only the last (2.1.280),
    which would drop mnemo's hooks from the child."""
    from mnemo.core import child_profile

    hooks = {"SessionEnd": [{"hooks": [{"type": "command", "command": "mnemo x"}]}]}
    monkeypatch.setattr(child_profile, "mnemo_hooks", lambda: hooks)
    monkeypatch.setattr(child_profile, "mnemo_mcp_servers", lambda: {})
    monkeypatch.delenv("MNEMO_DISPATCH_FULL_PROFILE", raising=False)
    run = _Run()
    monkeypatch.setattr(dispatch.subprocess, "run", run)

    dispatch.spawn_child("P", cwd=tmp_path, settings=twins.SETTINGS)
    assert _settings_files(run.args) == [{"hooks": hooks, "autoMemoryEnabled": False}]
    assert run.args[-1] == "P"

    dispatch.spawn_child("P", cwd=tmp_path, lean=False, settings=twins.SETTINGS)
    assert _settings_files(run.args) == [{"autoMemoryEnabled": False}]
    assert "--setting-sources" not in run.args


@pytest.mark.real_spawn
def test_without_settings_the_argv_and_the_file_are_what_they_were(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mnemo.core import child_profile

    hooks = {"Stop": []}
    monkeypatch.setattr(child_profile, "mnemo_hooks", lambda: hooks)
    monkeypatch.setattr(child_profile, "mnemo_mcp_servers", lambda: {})
    monkeypatch.delenv("MNEMO_DISPATCH_FULL_PROFILE", raising=False)
    run = _Run()
    monkeypatch.setattr(dispatch.subprocess, "run", run)

    dispatch.spawn_child("P", cwd=tmp_path)
    assert _settings_files(run.args) == [{"hooks": hooks}]
    dispatch.spawn_child("P", cwd=tmp_path, lean=False)
    assert run.args == ["claude", "--bg", "P"]


def test_show_puts_each_twins_closing_report_under_its_diff_scrubbed_alike(
    vault: Path, repo: Path, spawned: Spawned, tmp_path: Path,
) -> None:
    pair_id, results = _dispatch(repo, vault)
    _work(results[0].worktree, "x = 1\n")          # the other commits nothing
    first = _transcript(tmp_path, "a", _prompt(), _say(_text("working")),
                        _say(_text("Done. Branch fix/issue-449-aaaaaa, id 00000001.")))
    second = _transcript(tmp_path, "b", _prompt(),
                         _say(_text("Already shipped in #251; nothing to change.")))

    out = twins.show(vault, pair_id, rng=random.Random(0),
                     state=_states(**{"00000001": first, "00000002": second}))

    order = twins.read_pairs(vault)[pair_id].order
    sections = dict(zip(("A", "B"), out.split("===== A =====")[1].split("===== B =====")))
    label_of_second = "A" if order[0] == "bbbbbb" else "B"
    assert "Already shipped in #251" in sections[label_of_second]
    assert "(no commits on this branch — its closing report says why)" in \
        sections[label_of_second]
    assert "delivered nothing" not in out
    other = "B" if label_of_second == "A" else "A"
    assert "Done. Branch <twin>, id <twin>." in sections[other]
    # The report comes after its own diff, never before it.
    assert sections[other].index("x = 1") < sections[other].index("closing report")


def test_a_twin_with_no_transcript_says_so_rather_than_showing_nothing(
    vault: Path, pair,
) -> None:
    pair_id, _ = pair
    out = twins.show(vault, pair_id, state=_done)
    assert out.count("(no closing report — its transcript is gone or holds no text)") == 2


def test_show_records_what_reached_each_twin_once(
    vault: Path, repo: Path, spawned: Spawned, tmp_path: Path,
) -> None:
    pair_id, results = _dispatch(repo, vault)
    for r in results:
        _work(r.worktree, "x = 1\n")
    asked = _transcript(
        tmp_path, "asked", _prompt(),
        _say(_tool("q1", "AskUserQuestion", questions=[{"question": "Drop it?"}])),
        _result("q1", 'Your questions have been answered: "Drop it?"="Yes".'),
        _say(_tool("w1", "Write",
                   file_path="/Users/x/.claude/projects/-Users-x-mnemo/memory/note.md",
                   content="...")),
        _result("w1", "File created"),
        _say(_text("Report.")),
    )
    looked = _transcript(
        tmp_path, "looked", _prompt(),
        _say(_tool("b1", "Bash", command="git worktree list")),
        _result("b1", f"{repo}  abc [master]\n{repo.parent}/mnemo-wt-449-aaaaaa  abc "
                      "[fix/issue-449-aaaaaa]\n"),
        _say(_text("Report.")),
    )
    state = _states(**{"00000001": asked, "00000002": looked})

    twins.show(vault, pair_id, state=state)
    twins.show(vault, pair_id, state=state)

    found = twins.read_pairs(vault)[pair_id].conditions
    assert found == {
        "aaaaaa": {"human_turns": 0, "answered_questions": 1, "saw_sibling": False,
                   "memory_writes": 1},
        "bbbbbb": {"human_turns": 0, "answered_questions": 0, "saw_sibling": True,
                   "memory_writes": 0},
    }
    events = [json.loads(l) for l in twins.log_path(vault).read_text(encoding="utf-8").splitlines()]
    assert sum(1 for e in events if e["event"] == "conditions") == 1


def test_a_pruned_transcript_is_unknown_not_clean_and_is_read_when_it_can_be(
    vault: Path, pair, tmp_path: Path,
) -> None:
    pair_id, _ = pair
    later = _transcript(tmp_path, "later", _prompt(), _say(_text("Report.")))
    twins.show(vault, pair_id, state=_states(**{"00000001": later}))
    assert set(twins.read_pairs(vault)[pair_id].conditions) == {"aaaaaa"}

    twins.show(vault, pair_id, state=_states(**{"00000001": later, "00000002": later}))
    assert set(twins.read_pairs(vault)[pair_id].conditions) == {"aaaaaa", "bbbbbb"}


def test_only_input_during_the_run_counts_as_a_person(tmp_path: Path) -> None:
    path = _transcript(
        tmp_path, "t",
        _prompt(),                                              # the prompt: not input
        _say(_tool("q1", "AskUserQuestion", questions=[])),
        _result("q1", "User declined to answer questions", is_error=True),
        _result("x1", "tool output"),                           # the loop, not a person
        _prompt("<mnemo-resume>the limit reset</mnemo-resume>"),  # mnemo waking it
        _prompt("<task-notification>done</task-notification>"),
        _prompt("go with the second option"),                   # a person
        _line({"type": "user", "isSidechain": True,
               "message": {"role": "user", "content": "subagent prompt"}}),
    )
    found = twins.conditions_of(path)
    assert (found["human_turns"], found["answered_questions"]) == (1, 0)
    assert twins.human_input(found) is True
    assert twins.human_input({"human_turns": 0, "answered_questions": 0}) is False
    assert twins.human_input(None) is None
    assert twins.conditions_of(tmp_path / "gone.jsonl") is None


def test_the_sibling_is_named_by_issue_and_tag_never_by_a_bare_tag(
    repo: Path, vault: Path, spawned: Spawned, tmp_path: Path,
) -> None:
    """A bare six-hex tag turns up inside any commit hash."""
    pair_id, _ = _dispatch(repo, vault)
    pair = twins.read_pairs(vault)[pair_id]
    a, b = pair.twins
    assert twins.sibling_names(pair, a) == ["-449-bbbbbb", "00000002"]

    hashes = _transcript(tmp_path, "h", _prompt(),
                         _say(_tool("b1", "Bash", command="git log")),
                         _result("b1", "commit 9bbbbbb1c0ffee\n"))
    assert twins.conditions_of(hashes, sibling=twins.sibling_names(pair, a))[
        "saw_sibling"] is False
    ps = _transcript(tmp_path, "ps", _prompt(),
                     _say(_tool("b1", "Bash", command="ps")),
                     _result("b1", "123 node /x/mnemo-wt-449-bbbbbb/node_modules/.bin/jest"))
    assert twins.conditions_of(ps, sibling=twins.sibling_names(pair, a))["saw_sibling"]


def test_the_cli_says_what_reached_each_twin_only_after_the_answer(
    vault: Path, pair, monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path,
    tmp_jobs_dir: Path,
) -> None:
    pair_id, _ = pair
    asked = _transcript(
        tmp_path, "asked", _prompt(),
        _say(_tool("q1", "AskUserQuestion", questions=[])),
        _result("q1", "Your questions have been answered: x"), _say(_text("Report.")),
    )
    monkeypatch.setattr(twins, "default_vault", lambda: vault)
    # Through the jobs dir, as the CLI reads it: `show`'s `state=` default is
    # bound when the module loads, so patching `twins._state` would not reach it.
    for short_id, data in (("00000001", {**_done(""), "linkScanPath": str(asked)}),
                           ("00000002", _done(""))):
        (tmp_jobs_dir / short_id).mkdir(parents=True)
        (tmp_jobs_dir / short_id / "state.json").write_text(json.dumps(data), encoding="utf-8")
    ns = lambda **kw: argparse.Namespace(**{"action": "list", "pair": None, "answer": None, **kw})

    assert twins_cmd.cmd_twins(ns(action="show", pair=pair_id)) == 0
    shown = capsys.readouterr().out
    assert "Report." in shown                 # the transcript really was read
    assert "question(s) answered" not in shown

    assert twins_cmd.cmd_twins(ns(action="prefer", pair=pair_id, answer="A")) == 0
    out = capsys.readouterr().out
    assert "1 question(s) answered" in out
