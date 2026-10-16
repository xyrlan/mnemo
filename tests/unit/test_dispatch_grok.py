"""``mnemo dispatch`` on Grok: a child is a session of Grok Build's leader.

No ``grok`` runs here. ``acp._grok_argv`` is pointed at
``_fake_grok_leader.py``, which answers with the shapes grok 1.0.60 answered
live (grok-acp, PR #710), and ``shutil.which("grok")`` is answered by the test.
What is held to the real thing:

- a Grok child gets the tree, branch, prompt, grant and parent link a Claude
  child gets, and a ``ChildAgent`` line keyed by ``grok.paths.short_id``;
- everything Grok cannot take is refused before any worktree exists;
- a spawn that fails leaves no tree and no branch behind;
- with no Grok anywhere, dispatch hands the spawn exactly the call it always
  did (decision 8 of ``design/specs/2026-10-16-grok-host.md``).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mnemo.cli.commands import dispatch as dispatch_cmd
from mnemo.core import contracts, dispatch
from mnemo.core.grok import acp
from mnemo.core.grok import paths as grok_paths
from mnemo.core.sessions import child_agents, grants, parents

FAKE = Path(__file__).with_name("_fake_grok_leader.py")

#: The real spawn, bound before the suite's autouse fixture stubs it per test.
REAL_SPAWN_CHILD = dispatch.spawn_child

PARENT = "870233c6-252f-4b55-ae1f-b675d4557ce8"
GROK_PARENT = "01a144cc-0000-7000-8000-00009ac0ffee"

#: What a real home's ``models_cache.json`` holds, minus everything not read.
MODELS_CACHE = {
    "fetched_at": "2026-10-16T13:31:53Z",
    "grok_version": "1.0.60",
    "models": {"grok-4.7": {"info": {
        "id": "grok-4.7", "hidden": False, "supports_reasoning_effort": True,
        "reasoning_effort": "high",
        "reasoning_efforts": [{"id": e, "value": e} for e in ("xhigh", "high", "medium", "low")],
    }}},
}

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_text("x\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "master"], ["add", "."], ["commit", "-qm", "i"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                       env={**os.environ, **GIT_ENV})
    return root


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    for module in (parents, grants, child_agents):
        monkeypatch.setattr(module, "_default_vault", lambda: root)
    return root


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    h = tmp_path / "gh"
    h.mkdir()
    (h / "auth.json").write_text("{}", encoding="utf-8")
    (h / "models_cache.json").write_text(json.dumps(MODELS_CACHE), encoding="utf-8")
    return h


@pytest.fixture()
def fake(tmp_path: Path, monkeypatch, home: Path, vault: Path):
    """The fake leader, the real spawn, and no session in the environment."""
    state_path = tmp_path / "fake-state.json"
    state_path.write_text(json.dumps({"sessions": {}}), encoding="utf-8")
    monkeypatch.setenv("FAKE_GROK_STATE", str(state_path))
    monkeypatch.setattr(acp, "_grok_argv", lambda: [sys.executable, str(FAKE)])
    monkeypatch.setattr(acp, "state_dir", lambda: tmp_path / "grok-state")
    monkeypatch.setattr(dispatch.shutil, "which",
                        lambda name, *a, **k: "/opt/grok/bin/grok" if name == "grok" else None)
    monkeypatch.setattr(dispatch, "spawn_child", REAL_SPAWN_CHILD)
    monkeypatch.setattr(dispatch, "_ensure_notice_watcher", lambda: None)
    for name in ("CLAUDE_CODE_SESSION_ID", "GROK_SESSION_ID", "MNEMO_AGENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROK_HOME", str(home))

    class Fake:
        @staticmethod
        def state() -> dict:
            return json.loads(state_path.read_text(encoding="utf-8"))

        @staticmethod
        def set(**kw) -> None:
            s = Fake.state()
            s.update(kw)
            state_path.write_text(json.dumps(s), encoding="utf-8")

        @staticmethod
        def only_session() -> tuple:
            sessions = Fake.state()["sessions"]
            assert len(sessions) == 1, sessions
            return next(iter(sessions.items()))

    return Fake


def _issue(number: int = 702) -> dispatch.Issue:
    return dispatch.Issue(number=number, title="grok spawn", body="Spawn on Grok.")


def _fetch(issue: int, *, repo_root) -> dispatch.Issue:  # noqa: ARG001
    return _issue(issue)


PIECE = contracts.Piece(slug="spawn", files=["src/a.py"], exposes=["`f() -> None`"])


def _branches(repo: Path) -> list:
    out = subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=repo,
                         capture_output=True, text=True, check=True).stdout
    return sorted(out.split())


@pytest.fixture()
def no_tree(monkeypatch) -> None:
    """Fail the test if a worktree is even attempted: "before" means before."""
    def refuse(*a, **k):
        raise AssertionError("a worktree was made for a dispatch Grok refuses")

    monkeypatch.setattr(dispatch, "ensure_worktree", refuse)


def _no_tree_and_no_branch(repo: Path, target) -> None:
    assert not dispatch.worktree_path(target, repo_root=repo).exists()
    assert _branches(repo) == ["master"]


# --- the child is a leader session ------------------------------------------


def test_a_grok_child_is_a_leader_session_in_its_tree_opened_with_its_prompt(
    repo: Path, fake, home: Path,
) -> None:
    result = dispatch.dispatch_issue(702, repo_root=repo, fetch=_fetch, agent="grok",
                                     may=grants.parse("pr"), effort="low")

    sid, session = fake.only_session()
    tree = dispatch.worktree_path(702, repo_root=repo)
    assert result.error is None and result.worktree == tree
    assert Path(session["cwd"]) == tree
    assert session["yolo"] is True  # its turn runs with nobody attached
    assert session["effort"] == "low"
    expected = dispatch.for_agent(
        dispatch.build_prompt(702, title="grok spawn", body="Spawn on Grok.",
                              repo_root=repo, may=grants.parse("pr")),
        "grok", may=grants.parse("pr"), issue=702,
    )
    assert session["prompts"] == [expected]
    assert result.session_id == sid
    assert result.short_id == grok_paths.short_id(sid) == sid.replace("-", "")[-8:]
    assert result.agent == "grok" and Path(result.home) == home
    assert _branches(repo) == ["fix/issue-702", "master"]


def test_a_grok_child_is_recorded_in_every_ledger_by_its_grok_short_id(
    repo: Path, fake, home: Path, vault: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", PARENT)

    result = dispatch.dispatch_issue(702, repo_root=repo, fetch=_fetch, agent="grok",
                                     may=grants.parse("push"))

    sid, _ = fake.only_session()
    key = grok_paths.short_id(sid)
    line = child_agents.read(vault)[key]
    assert (line.agent, line.session_id) == ("grok", sid)
    assert Path(line.home) == home and Path(line.cwd) == result.worktree
    assert parents.read(vault) == {key: PARENT}
    assert grants.read(vault) == {key: ("push",)}


def test_a_piece_on_grok_gets_its_branch_and_its_own_model(repo: Path, fake) -> None:
    piece = contracts.Piece(slug="spawn", files=["src/a.py"], exposes=["`f()`"],
                            model="grok-4.7")

    result = dispatch.dispatch_piece(piece, feature="grok-host-w2", repo_root=repo,
                                     agent="grok")

    _, session = fake.only_session()
    assert result.error is None and session["model"] == "grok-4.7"
    assert "feat/grok-host-w2/spawn" in _branches(repo)
    assert "claude" not in session["prompts"][0].lower()


def test_dispatch_all_on_grok_spawns_each_issue(repo: Path, fake) -> None:
    results = dispatch.dispatch_all([1, 2], repo_root=repo, fetch=_fetch, agent="grok")

    assert [r.error for r in results] == [None, None]
    assert len(fake.state()["sessions"]) == 2
    assert len({r.short_id for r in results}) == 2


# --- the prompt -------------------------------------------------------------


@pytest.mark.parametrize("may", [(), ("push",), ("push", "pr")], ids=["none", "push", "pr"])
def test_the_grok_prompt_is_the_claude_one_with_a_closing_that_names_no_claude_command(
    may: tuple,
) -> None:
    claude = dispatch.build_prompt(9, title="t", body="b", may=may)
    grok = dispatch.for_agent(claude, "grok", may=may, issue=9)

    head = claude[: claude.index("How to finish")]
    assert grok.startswith(head)
    assert "claude" not in grok.lower()
    tail = grok[len(head):]
    # The report is the last thing the turn says; nothing asks for a stop.
    assert tail.index("Ask git") < tail.index("closing report")
    assert "stop" not in tail.lower()


def test_a_piece_prompt_and_a_read_only_prompt_retarget_the_same_way() -> None:
    piece = dispatch.build_piece_prompt(PIECE, feature="f", may=("push", "pr"))
    assert "claude" not in dispatch.for_agent(piece, "grok", may=("push", "pr")).lower()

    analysis = dispatch.build_prompt(9, title="t", body="b", read_only=True)
    grok = dispatch.for_agent(analysis, "grok", read_only=True, issue=9)
    assert "gh issue comment 9" in grok and "claude" not in grok.lower()


def test_claude_keeps_its_prompt_byte_for_byte() -> None:
    prompt = dispatch.build_prompt(9, title="t", body="b", may=("push",))

    assert dispatch.for_agent(prompt, "claude", may=("push",), issue=9) is prompt


def test_a_prompt_rendered_with_another_grant_is_refused_not_given_two_closings() -> None:
    prompt = dispatch.build_prompt(9, title="t", body="b", may=("push",))

    with pytest.raises(ValueError):
        dispatch.for_agent(prompt, "grok", may=(), issue=9)


# --- refused before any worktree exists -------------------------------------


@pytest.mark.parametrize("kwargs, words", [
    ({"read_only": True}, "--read-only: not supported on Grok yet"),
    ({"model": "haiku"}, "Grok has no model 'haiku'"),
    ({"effort": "max"}, "Grok's grok-4.7 takes no effort 'max'"),
    ({"effort": "huge"}, "Grok takes no effort 'huge'"),
    ({"model": "grok-4.7", "effort": "max"}, "takes xhigh, high, medium, low"),
], ids=["read-only", "claude-model", "effort-the-model-lacks", "unknown-effort", "named-model"])
def test_what_grok_cannot_take_is_refused_before_any_tree(
    repo: Path, fake, no_tree, kwargs: dict, words: str,
) -> None:
    with pytest.raises(dispatch.DispatchError, match=words.replace("(", r"\(")):
        dispatch.dispatch_issue(702, repo_root=repo, fetch=_fetch, agent="grok", **kwargs)

    _no_tree_and_no_branch(repo, 702)
    assert fake.state()["sessions"] == {}


def test_no_grok_on_path_is_refused_with_what_to_do(repo: Path, fake, no_tree, monkeypatch) -> None:
    monkeypatch.setattr(dispatch.shutil, "which", lambda *a, **k: None)

    with pytest.raises(dispatch.DispatchError, match="grok is not on PATH.*--agent claude"):
        dispatch.dispatch_issue(702, repo_root=repo, fetch=_fetch, agent="grok")
    _no_tree_and_no_branch(repo, 702)


def test_a_home_with_no_login_is_refused_with_the_login_command(
    repo: Path, fake, no_tree, home: Path,
) -> None:
    (home / "auth.json").unlink()

    with pytest.raises(dispatch.DispatchError, match="grok login"):
        dispatch.dispatch_issue(702, repo_root=repo, fetch=_fetch, agent="grok")
    _no_tree_and_no_branch(repo, 702)


def test_a_missing_home_is_refused(repo: Path, fake, no_tree, tmp_path: Path) -> None:
    with pytest.raises(dispatch.DispatchError, match="does not exist"):
        dispatch.dispatch_issue(702, repo_root=repo, fetch=_fetch, agent="grok",
                                home=tmp_path / "nowhere")


def test_dispatch_all_reports_a_refusal_for_every_issue_and_reads_none(
    repo: Path, fake,
) -> None:
    def no_fetch(issue, *, repo_root):  # noqa: ARG001
        raise AssertionError("an issue was read for a dispatch Grok refused")

    results = dispatch.dispatch_all([1, 2], repo_root=repo, fetch=no_fetch,
                                    agent="grok", model="opus")

    assert [r.issue for r in results] == [1, 2]
    assert all("Grok has no model 'opus'" in r.error for r in results)


def test_one_piece_grok_cannot_run_refuses_the_whole_contract(repo: Path, fake, no_tree) -> None:
    contract = contracts.Contract(feature="f", verdict="parallel", pieces=[
        contracts.Piece(slug="good", files=["a"], exposes=["x"]),
        contracts.Piece(slug="bad", files=["b"], exposes=["y"], model="sonnet"),
    ])

    with pytest.raises(dispatch.DispatchError, match="bad: --model"):
        dispatch.dispatch_contract(contract, repo_root=repo, agent="grok")
    assert not dispatch.worktree_path("c-good", repo_root=repo).exists()
    assert fake.state()["sessions"] == {}


def test_a_dispatch_wide_model_every_piece_overrides_is_not_refused(home: Path, fake) -> None:
    contract = contracts.Contract(feature="f", verdict="parallel", pieces=[
        contracts.Piece(slug="a", files=["a"], exposes=["x"], model="grok-4.7"),
    ])

    assert dispatch.check_grok_contract(contract, model="sonnet") == home
    with pytest.raises(dispatch.DispatchError, match="a: --effort"):
        dispatch.check_grok_contract(contract, effort="max")


def test_a_read_only_piece_refuses_its_contract_on_grok(repo: Path, fake, no_tree) -> None:
    contract = contracts.Contract(feature="f", verdict="parallel", pieces=[
        contracts.Piece(slug="look", files=["a"], exposes=["x"], read_only=True),
    ])

    with pytest.raises(dispatch.DispatchError, match="look: --read-only"):
        dispatch.dispatch_contract(contract, repo_root=repo, agent="grok")


# --- the vocabulary Grok recorded -------------------------------------------


def test_the_vocabulary_is_read_from_grok_s_own_model_list(home: Path) -> None:
    vocabulary = dispatch.grok_vocabulary(home)

    assert vocabulary.models == {"grok-4.7": ("xhigh", "high", "medium", "low")}
    assert vocabulary.default == "grok-4.7"


def test_custom_models_and_the_configured_default_count(home: Path, monkeypatch) -> None:
    monkeypatch.setattr(dispatch.shutil, "which", lambda *a, **k: "/opt/grok/bin/grok")
    (home / "config.toml").write_text(
        '[models]\ndefault = "my-local"\n\n[model."my-local"]\nbase_url = "http://x"\n'
        "[model.other]\nmodel = \"o\"\n", encoding="utf-8")

    vocabulary = dispatch.grok_vocabulary(home)

    assert vocabulary.default == "my-local"
    assert vocabulary.models["my-local"] is None and vocabulary.models["other"] is None
    # A custom model's efforts are unknown, so no effort Grok parses is refused
    # on it here — and the configured default is what an unnamed model runs.
    assert dispatch.check_grok("my-local", "max", home=home) == home
    assert dispatch.check_grok(None, "max", home=home) == home
    with pytest.raises(dispatch.DispatchError):
        dispatch.check_grok("grok-4.7", "max", home=home)


def test_a_home_with_no_model_list_leaves_the_model_to_the_leader(
    repo: Path, fake, home: Path,
) -> None:
    (home / "models_cache.json").unlink()
    assert dispatch.grok_vocabulary(home) is None

    # Not refused before the tree: with no list there is nothing to check
    # against. Grok would swap the model silently; `Leader.new_session`
    # refuses it at spawn instead, and the tree made for it is rolled back.
    with pytest.raises(dispatch.DispatchError, match="Grok has no model 'haiku'"):
        dispatch.dispatch_issue(702, repo_root=repo, fetch=_fetch, agent="grok",
                                model="haiku")
    _no_tree_and_no_branch(repo, 702)


# --- a spawn that fails leaves nothing half-made ----------------------------


def test_a_leader_that_refuses_the_login_leaves_no_tree(repo: Path, fake) -> None:
    fake.set(logged_in=False)  # auth.json present, but Grok says no

    with pytest.raises(dispatch.DispatchError, match="grok login"):
        dispatch.dispatch_issue(702, repo_root=repo, fetch=_fetch, agent="grok")
    _no_tree_and_no_branch(repo, 702)


def test_a_leader_that_does_not_come_up_leaves_no_tree_and_says_how_to_look(
    repo: Path, fake,
) -> None:
    fake.set(exit_on_initialize=True)

    with pytest.raises(dispatch.DispatchError, match="did not come up.*grok agent --leader stdio"):
        dispatch.dispatch_issue(702, repo_root=repo, fetch=_fetch, agent="grok")
    _no_tree_and_no_branch(repo, 702)


def test_a_turn_that_fails_closes_its_session_and_leaves_no_tree(
    repo: Path, fake, vault: Path, monkeypatch,
) -> None:
    def rate_limited(self, session_id, cwd, text, **kw):  # noqa: ARG001
        raise acp.RateLimited("session/prompt", {"message": "Rate limited"})

    monkeypatch.setattr(acp.Leader, "prompt", rate_limited)

    with pytest.raises(dispatch.DispatchError, match="past its usage limit"):
        dispatch.dispatch_issue(702, repo_root=repo, fetch=_fetch, agent="grok")

    _, session = fake.only_session()
    assert session["resident"] is False  # closed, not left idle in the leader
    _no_tree_and_no_branch(repo, 702)
    assert child_agents.read(vault) == {}


def test_a_turn_not_seen_taken_keeps_its_tree_and_is_recorded_with_a_warning(
    repo: Path, fake, vault: Path, monkeypatch,
) -> None:
    def slow(self, session_id, cwd, text, **kw):  # noqa: ARG001
        raise TimeoutError("did not take the turn in time")

    monkeypatch.setattr(acp.Leader, "prompt", slow)

    result = dispatch.dispatch_issue(702, repo_root=repo, fetch=_fetch, agent="grok")

    sid, _ = fake.only_session()
    assert result.error is None and result.worktree.exists()
    assert "had not taken the opening turn" in result.warning
    assert grok_paths.short_id(sid) in child_agents.read(vault)


# --- Claude is what it was (decision 8) -------------------------------------


def test_claude_hands_the_spawn_exactly_the_call_it_always_did(
    repo: Path, vault: Path, monkeypatch,
) -> None:
    calls = []

    def capture(prompt, **kwargs):
        calls.append(kwargs)
        return "a1b2c3d4"

    monkeypatch.setattr(dispatch, "spawn_child", capture)
    monkeypatch.setattr(dispatch.claude_cli, "verify_registered", lambda *a, **k: None)
    monkeypatch.setattr(dispatch, "_ensure_notice_watcher", lambda: None)

    result = dispatch.dispatch_issue(702, repo_root=repo, fetch=_fetch)

    tree = dispatch.worktree_path(702, repo_root=repo)
    assert calls == [{"cwd": tree, "model": None, "lean": True, "effort": None,
                      "read_only": False}]
    assert (result.agent, result.session_id, result.home) == ("claude", None, None)
    assert not child_agents.log_path(vault).exists()


# --- the dispatcher's own session -------------------------------------------


def test_a_grok_dispatcher_s_id_wins_over_an_inherited_claude_one() -> None:
    env = {"GROK_SESSION_ID": GROK_PARENT, "CLAUDE_CODE_SESSION_ID": PARENT}

    assert parents.parent_from_env(env) == GROK_PARENT
    assert parents.parent_from_env({"CLAUDE_CODE_SESSION_ID": PARENT}) == PARENT
    assert parents.parent_from_env({"GROK_SESSION_ID": "  ", "CLAUDE_CODE_SESSION_ID": PARENT}) == PARENT
    assert parents.parent_from_env({"GROK_SESSION_ID": ""}) is None


def test_a_grok_parent_is_recorded_once_with_its_home_and_its_session_s_cwd(
    tmp_path: Path, vault: Path, home: Path,
) -> None:
    filed_under = str(Path("/work/proj"))
    session = home / "sessions" / "%2Fwork%2Fproj" / GROK_PARENT
    session.mkdir(parents=True)
    (session / "summary.json").write_text(json.dumps({"info": {"cwd": filed_under}}),
                                          encoding="utf-8")
    env = {"GROK_SESSION_ID": GROK_PARENT, "GROK_HOME": str(home)}

    assert parents.record("aaaaaaaa", env=env) == GROK_PARENT
    assert parents.record("bbbbbbbb", env=env) == GROK_PARENT

    lines = child_agents.log_path(vault).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    line = child_agents.read(vault)[grok_paths.short_id(GROK_PARENT)]
    assert (line.agent, line.session_id, line.cwd) == ("grok", GROK_PARENT, filed_under)
    assert Path(line.home) == home
    assert parents.read(vault) == {"aaaaaaaa": GROK_PARENT, "bbbbbbbb": GROK_PARENT}


def test_a_grok_parent_whose_session_is_not_on_disk_falls_back_to_its_workspace(
    vault: Path, home: Path,
) -> None:
    root = str(Path("/work/elsewhere"))
    parents.record("aaaaaaaa", env={"GROK_SESSION_ID": GROK_PARENT, "GROK_HOME": str(home),
                                    "GROK_WORKSPACE_ROOT": root})

    assert child_agents.read(vault)[grok_paths.short_id(GROK_PARENT)].cwd == root


def test_a_claude_parent_writes_the_link_it_always_did_and_nothing_else(vault: Path) -> None:
    parents.record("a1b2c3d4", env={"CLAUDE_CODE_SESSION_ID": PARENT})

    assert parents.log_path(vault).read_text(encoding="utf-8") == (
        '{"short_id": "a1b2c3d4", "parent_session": "870233c6-252f-4b55-ae1f-b675d4557ce8"}\n'
    )
    assert not child_agents.log_path(vault).exists()


# --- the command ------------------------------------------------------------


def _args(**kw) -> argparse.Namespace:
    fields = dict(issues=[702], contract=None, example=False, dry_run=False, model=None,
                  effort=None, may=None, read_only=False, full_profile=False,
                  agent=None, twins=False)
    fields.update(kw)
    return argparse.Namespace(**fields)


@pytest.fixture()
def in_repo(repo: Path, monkeypatch) -> Path:
    monkeypatch.setattr(dispatch_cmd, "_repo_root", lambda: repo)
    return repo


def test_twins_on_grok_is_refused_with_one_line(in_repo: Path, fake, no_tree, capsys) -> None:
    assert dispatch_cmd.cmd_dispatch(_args(twins=True, agent="grok")) == 1

    assert capsys.readouterr().out.splitlines() == [dispatch_cmd.TWINS_ON_GROK]
    _no_tree_and_no_branch(in_repo, 702)


def test_the_account_mnemo_desktop_selected_is_the_one_dispatched_on(
    in_repo: Path, fake, monkeypatch, capsys,
) -> None:
    monkeypatch.setenv("MNEMO_AGENT", "grok")

    assert dispatch_cmd.cmd_dispatch(_args(dry_run=True)) == 0

    out = capsys.readouterr().out
    assert "fix/issue-702" in out and "[grok]" in out


def test_a_grok_dry_run_refuses_what_the_spawn_would(in_repo: Path, fake, no_tree, capsys) -> None:
    assert dispatch_cmd.cmd_dispatch(_args(dry_run=True, agent="grok", read_only=True)) == 1

    assert capsys.readouterr().out.strip() == dispatch.GROK_READ_ONLY_REFUSAL


def test_a_claude_dry_run_row_is_unchanged(in_repo: Path, vault: Path, monkeypatch, capsys) -> None:
    for name in ("MNEMO_AGENT", "GROK_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)

    assert dispatch_cmd.cmd_dispatch(_args(dry_run=True, effort="high")) == 0

    tree = dispatch.worktree_path(702, repo_root=in_repo)
    assert capsys.readouterr().out == f"#702  fix/issue-702  {tree}  [effort high]  may: push+pr\n"


def test_an_unknown_agent_in_the_environment_is_refused(in_repo: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MNEMO_AGENT", "codex")

    assert dispatch_cmd.cmd_dispatch(_args(dry_run=True)) == 1
    assert "unknown agent CLI 'codex'" in capsys.readouterr().out


def test_the_report_names_grok_s_command_to_open_the_child(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    for name in ("CLAUDE_CODE_SESSION_ID", "GROK_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    sid = "01a144cc-77f5-7ea3-9060-06013224200f"
    tree = tmp_path / "proj-wt-702"
    account = tmp_path / "grok-work"
    result = dispatch.Dispatched(issue=702, worktree=tree, short_id="3224200f",
                                 agent="grok", session_id=sid, home=str(account))

    assert dispatch_cmd._report([result]) == 0

    out = capsys.readouterr().out
    assert f"#702  3224200f  {tree}  [grok]" in out
    attach = next(line for line in out.splitlines() if "attach:" in line)
    assert attach.strip() == f"attach: {dispatch_cmd.grok_attach_command(result)}"
    assert attach.endswith(f"grok --resume {sid}")
    assert "GROK_HOME=" in attach  # not Grok's default home: resume must name it
    assert "claude" not in out.lower()
    assert "profile:" not in out


def test_the_attach_command_names_no_home_for_grok_s_default(tmp_path: Path) -> None:
    result = dispatch.Dispatched(issue=702, worktree=tmp_path / "t", short_id="3224200f",
                                 agent="grok", session_id="x",
                                 home=str(Path.home() / ".grok"))

    assert "GROK_HOME" not in dispatch_cmd.grok_attach_command(result)
