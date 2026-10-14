"""#661: a fresh-context reviewer at two checkpoints inside the child.

The switch both ways (byte-identical prompt and argv when off), the design
trigger and its skip line, the ledger row and the PR-body section, ``mnemo
review record``, and the A/B twins arms with ``show`` staying blind.

The spawn is stubbed at ``dispatch.spawn_child`` as in
``test_dispatch_twins.py``; git is real, because the two arms' trees and base
commit are part of what is claimed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import subprocess
from pathlib import Path
from typing import Dict, List

import pytest

from mnemo.cli.commands import dispatch as dispatch_cmd
from mnemo.cli.commands import review as review_cmd
from mnemo.cli.commands import twins as twins_cmd
from mnemo.core import claude_cli, dispatch, review, twins

ISSUE = dict(title="measure the thing", body="The body, verbatim.")

#: sha256 of ``build_prompt(449, **ISSUE, ...)`` as master rendered it before
#: #661 (641eac5), for the three shapes a child is given.
BEFORE = {
    "plain": "fe321ff9890efa3166bbc892989c312053569d34b8c4edbb7bcb6ab04f969292",
    "pr_changelog": "0bf8d67519ab24d5d8769555e884776ae6b8c0e97dae032570273f3e031d4700",
    "blind": "51869914eec56bf6712314655423fe79b6bc408b2a7248ab902b4f9a9bb4f2da",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run(args, *, cwd) -> str:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                          check=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "mnemo"
    root.mkdir()
    _run(["git", "init", "-b", "master"], cwd=root)
    _run(["git", "config", "user.email", "t@example.com"], cwd=root)
    _run(["git", "config", "user.name", "t"], cwd=root)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=root)
    _run(["git", "commit", "-m", "base"], cwd=root)
    (root / "changelog.d").mkdir()
    return root


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


class Spawned:
    def __init__(self) -> None:
        self.calls: List[Dict] = []

    def __call__(self, prompt, *, cwd, model=None, lean=True, effort=None,
                 read_only=False, settings=None, **extra):
        self.calls.append({"prompt": prompt, "cwd": Path(cwd), "read_only": read_only,
                           "settings": settings, **extra})
        return f"{len(self.calls):08x}"


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> Spawned:
    rec = Spawned()
    monkeypatch.setattr(dispatch, "spawn_child", rec)
    monkeypatch.setattr(claude_cli, "verify_registered", lambda short_id, *, cwd: None)
    monkeypatch.setattr(dispatch.parents, "record", lambda short_id: None)
    monkeypatch.setattr(dispatch.grants, "record", lambda short_id, may: None)
    monkeypatch.setattr(dispatch, "_ensure_notice_watcher", lambda: None)
    return rec


def _issue(number, *, repo_root):
    return dispatch.Issue(number=number, **ISSUE)


# --- the switch, off: nothing moved ------------------------------------------


def test_off_the_prompt_is_byte_identical_to_before(repo: Path) -> None:
    assert _sha(dispatch.build_prompt(449, **ISSUE)) == BEFORE["plain"]
    assert _sha(dispatch.build_prompt(449, **ISSUE, repo_root=repo,
                                      may=("push", "pr"))) == BEFORE["pr_changelog"]
    assert _sha(dispatch.build_prompt(449, **ISSUE, blind=True,
                                      repo_root=repo)) == BEFORE["blind"]
    assert _sha(dispatch.build_prompt(449, **ISSUE, review=())) == BEFORE["plain"]


def test_off_the_spawn_gets_no_agents_and_the_argv_is_unchanged(
    repo: Path, spawned: Spawned,
) -> None:
    dispatch.dispatch_issue(449, repo_root=repo, fetch=_issue)
    assert "agents" not in spawned.calls[0]
    assert review.AGENT_NAME not in spawned.calls[0]["prompt"]


@pytest.mark.real_spawn
def test_spawn_child_adds_agents_only_when_given(monkeypatch, tmp_path: Path) -> None:
    seen = []

    def run(args, **kw):
        seen.append(list(args))
        return subprocess.CompletedProcess(args, 0, "backgrounded · 1a2b3c4d\n", "")

    monkeypatch.setattr(dispatch.subprocess, "run", run)
    dispatch.spawn_child("P", cwd=tmp_path, lean=False)
    dispatch.spawn_child("P", cwd=tmp_path, lean=False, agents='{"x": {}}')
    assert seen[0] == ["claude", "--bg", "P"]
    assert seen[1] == ["claude", "--bg", "--agents", '{"x": {}}', "P"]


# --- the switch, on ----------------------------------------------------------


def test_on_with_the_gate_passed_the_prompt_carries_both_checkpoints() -> None:
    prompt = dispatch.build_prompt(449, **ISSUE, review=review.checkpoints(gate="pass"))
    flat = " ".join(prompt.split())
    assert "Design checkpoint, after exploring and before writing code" in prompt
    assert "Pre-PR checkpoint, on the finished diff" in flat
    assert f"subagent_type `{review.AGENT_NAME}`" in flat
    assert "mnemo review record" in flat and review.HEADING in flat
    # The clause sits before the closing steps, which stay last.
    assert prompt.index("Design checkpoint") < prompt.index("How to finish")
    plain = dispatch.build_prompt(449, **ISSUE)
    assert prompt.replace(review.checkpoint_clause(review.checkpoints(gate="pass")), "") == plain


def test_without_the_gate_only_the_design_checkpoint_is_given() -> None:
    assert review.checkpoints(gate="fail") == (review.DESIGN,)
    assert review.checkpoints(gate="pending") == (review.DESIGN,)
    prompt = dispatch.build_prompt(449, **ISSUE, review=review.checkpoints(gate="fail"))
    assert "Design checkpoint" in prompt and "Pre-PR checkpoint" not in prompt
    assert "At one point in this task" in " ".join(prompt.split())


def test_the_design_trigger_and_the_skip_line_are_pinned() -> None:
    clause = " ".join(review.checkpoint_clause((review.DESIGN,)).split())
    assert ("Run it only when the change touches persisted data or a schema, a "
            "public API or CLI, a UI flow, or concurrency/scale.") in clause
    assert "never into the tree" in clause
    assert "When none applies, do not run it; record " \
           "`design checkpoint: skipped, no design decision`." in clause
    assert review.DESIGN_SKIP == "design checkpoint: skipped, no design decision"


def test_on_the_child_is_spawned_with_the_reviewer(repo: Path, spawned: Spawned) -> None:
    dispatch.dispatch_issue(449, repo_root=repo, fetch=_issue, review=True)
    call = spawned.calls[0]
    agent = json.loads(call["agents"])[review.AGENT_NAME]
    assert agent["model"] == "inherit"
    assert agent["prompt"] == review.REVIEWER_PROMPT
    assert not {"Edit", "Write", "NotebookEdit", "MultiEdit"} & set(agent["tools"])
    assert review.checkpoint_clause(review.checkpoints()) in call["prompt"]


def test_a_read_only_child_gets_no_review(repo: Path, spawned: Spawned) -> None:
    dispatch.dispatch_issue(449, repo_root=repo, fetch=_issue, review=True, read_only=True)
    call = spawned.calls[0]
    assert "agents" not in call and review.AGENT_NAME not in call["prompt"]


def test_the_reviewer_judges_by_written_standards_and_writes_nothing() -> None:
    text = " ".join(review.REVIEWER_PROMPT.split())
    for needed in ("the vault's rules", "CLAUDE.md", "cited as file:line",
                   "blast radius", "suggested issue", "Do not write, edit, create or delete",
                   "suggestions: taste with no anchor"):
        assert needed in text


# --- the record: ledger and PR body ------------------------------------------

OUTCOME = {
    "design": "schema",
    "findings": [
        {"checkpoint": "design", "category": "schema", "anchor": "src/a.py:12",
         "finding": "the row has no version | field", "outcome": "accepted",
         "reason": "added one"},
        {"checkpoint": "pre-pr", "category": "bug", "anchor": "CLAUDE.md:30",
         "finding": "path compared as string", "outcome": "rejected",
         "reason": "it is a Path already"},
    ],
    "suggestions": ["nicer names"],
    "suggested_issues": ["split the module"],
}


def test_a_ledger_row_per_finding_with_who_recorded_it(vault: Path, tmp_path: Path) -> None:
    tree = tmp_path / "mnemo-wt-449-3fa9c1"
    context = review.context_of(tree, env={"CLAUDE_CODE_SESSION_ID": "deadbeef-0000"})
    written = review.record(vault, review.parse_record(json.dumps(OUTCOME)), context)

    rows = review.read_ledger(vault)
    assert written == 2 and len(rows) == 2
    assert review.ledger_path(vault) == vault / ".mnemo" / "review-ledger.jsonl"
    first = rows[0]
    for key, value in {"checkpoint": "design", "category": "schema",
                       "anchor": "src/a.py:12", "outcome": "accepted",
                       "reason": "added one", "issue": 449, "twin_tag": "3fa9c1",
                       "session": "deadbeef"}.items():
        assert first[key] == value
    assert rows[1]["outcome"] == "rejected"


@pytest.mark.parametrize(("bad", "says"), [
    ({"findings": [{"checkpoint": "design", "outcome": "accepted"}]}, "reason"),
    ({"findings": [{"checkpoint": "design", "outcome": "maybe", "reason": "r"}]}, "accepted or rejected"),
    ({"findings": [{"checkpoint": "later", "outcome": "accepted", "reason": "r"}]}, "checkpoint"),
    ({"design": "colour"}, "design"),
])
def test_an_outcome_out_of_shape_is_refused_saying_what(bad, says) -> None:
    with pytest.raises(review.RecordError, match=says):
        review.parse_record(json.dumps(bad))


def test_the_pr_body_section_has_the_fixed_heading_and_every_decision() -> None:
    section = review.pr_section(review.parse_record(json.dumps(OUTCOME)))
    assert section.startswith("## Review checkpoints\n")
    assert "- design checkpoint: ran (schema)" in section
    assert "| design | schema | src/a.py:12 | the row has no version \\| field | accepted | added one |" in section
    assert "| pre-pr | bug | CLAUDE.md:30 |" in section and "| rejected |" in section
    assert "- nicer names" in section and "- split the module" in section


def test_a_skipped_design_says_the_pinned_line() -> None:
    section = review.pr_section(review.parse_record("{}"))
    assert f"- {review.DESIGN_SKIP}" in section and "- no findings" in section


def test_mnemo_review_record_keeps_the_ledger_prints_the_section_and_leaves_the_tree(
    vault: Path, tmp_path: Path, monkeypatch, capsys,
) -> None:
    tree = tmp_path / "mnemo-wt-449"
    tree.mkdir()
    monkeypatch.chdir(tree)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(OUTCOME)))
    code = review_cmd.cmd_review(argparse.Namespace(action="record", vault=str(vault)))
    out = capsys.readouterr().out
    assert code == 0 and out.startswith(review.HEADING)
    assert [r["issue"] for r in review.read_ledger(vault)] == [449, 449]
    assert list(tree.iterdir()) == []


# --- blind twins, two arms ---------------------------------------------------


def _pick_first(tags):
    return tags[0]


def _arms(repo: Path, vault: Path, pick=_pick_first):
    tags = iter(("aaaaaa", "bbbbbb"))
    return twins.dispatch_twins(449, repo_root=repo, fetch=_issue, vault_root=vault,
                                draw=lambda: next(tags), arms=True, pick=pick)


def test_arms_give_one_twin_the_checkpoints_and_record_each_prompts_sha(
    repo: Path, vault: Path, spawned: Spawned,
) -> None:
    pair_id, _ = _arms(repo, vault)
    pair = twins.read_pairs(vault)[pair_id]
    by_tag = {c["cwd"].name.rsplit("-", 1)[1]: c for c in spawned.calls}

    assert pair.arms
    assert {t.tag: t.arm for t in pair.twins} == {"aaaaaa": "review", "bbbbbb": "control"}
    for twin in pair.twins:
        assert twin.prompt_sha256 == _sha(by_tag[twin.tag]["prompt"])
    assert by_tag["bbbbbb"]["prompt"] == dispatch.build_prompt(
        449, **ISSUE, repo_root=repo, blind=True)
    assert "agents" not in by_tag["bbbbbb"]
    assert review.AGENT_NAME in json.loads(by_tag["aaaaaa"]["agents"])
    # Only the checkpoint clause differs.
    assert by_tag["aaaaaa"]["prompt"].replace(
        review.checkpoint_clause(review.checkpoints()), "") == by_tag["bbbbbb"]["prompt"]
    # Both twins still get the same settings, base and no grant.
    assert by_tag["aaaaaa"]["settings"] == by_tag["bbbbbb"]["settings"] == twins.SETTINGS


def test_the_reviewed_arm_is_drawn(repo: Path, vault: Path, spawned: Spawned) -> None:
    pair_id, _ = _arms(repo, vault, pick=lambda tags: tags[1])
    pair = twins.read_pairs(vault)[pair_id]
    assert pair.twin("bbbbbb").arm == "review"


def test_a_plain_pair_records_no_arms(repo: Path, vault: Path, spawned: Spawned) -> None:
    tags = iter(("aaaaaa", "bbbbbb"))
    pair_id, _ = twins.dispatch_twins(449, repo_root=repo, fetch=_issue, vault_root=vault,
                                      draw=lambda: next(tags))
    pair = twins.read_pairs(vault)[pair_id]
    assert not pair.arms and {t.arm for t in pair.twins} == {""}
    assert spawned.calls[0]["prompt"] == spawned.calls[1]["prompt"]


def _line(obj) -> str:
    return json.dumps(obj) + "\n"


def _report_transcript(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / f"{name}.jsonl"
    path.write_text(
        _line({"type": "user", "message": {"role": "user", "content": "Work on #449"},
               "origin": {"kind": "human"}})
        + _line({"type": "assistant", "message": {"role": "assistant",
                                                  "content": [{"type": "text", "text": text}]}}),
        encoding="utf-8")
    return path


def _work(tree: Path, text: str) -> None:
    (tree / "fix.py").write_text(text, encoding="utf-8")
    _run(["git", "add", "fix.py"], cwd=tree)
    _run(["git", "commit", "-m", "fix"], cwd=tree)


def _states(**paths: Path):
    def state(short_id):
        data = {"state": "done", "tokens": 1000, "createdAt": "2026-10-14T10:00:00Z",
                "firstTerminalAt": "2026-10-14T10:14:00Z"}
        if short_id in paths:
            data["linkScanPath"] = str(paths[short_id])
        return data
    return state


REVIEWED_REPORT = (
    "Done: fixed the thing.\n\n"
    + review.pr_section(review.parse_record(json.dumps(OUTCOME)))
    + "\n## Next\nnothing left.\n"
)


def test_show_strips_the_review_section_and_says_nothing_of_the_arm(
    repo: Path, vault: Path, spawned: Spawned, tmp_path: Path,
) -> None:
    pair_id, results = _arms(repo, vault)
    _work(results[0].worktree, "x = 1\n")
    _work(results[1].worktree, "x = 2\n")
    state = _states(**{
        "00000001": _report_transcript(tmp_path, "a", REVIEWED_REPORT),
        "00000002": _report_transcript(tmp_path, "b", "Done: fixed the thing.\n\n## Next\nnothing left.\n"),
    })
    out = twins.show(vault, pair_id, rng=random.Random(0), state=state)

    assert twins.read_pairs(vault)[pair_id].order        # shown
    assert review.markers_in(out) == []
    for word in ("review", "control", "arm"):
        assert f" {word}" not in out.lower().replace("ready for review", "")
    assert out.count("Done: fixed the thing.") == 2 and out.count("## Next") == 2


def test_show_refuses_while_a_review_marker_is_left_in_a_report(
    repo: Path, vault: Path, spawned: Spawned, tmp_path: Path,
) -> None:
    pair_id, results = _arms(repo, vault)
    for r in results:
        _work(r.worktree, "x = 1\n")
    state = _states(**{"00000001": _report_transcript(
        tmp_path, "a", "Done. The design checkpoint agreed with me.\n")})

    with pytest.raises(twins.TwinsError, match="design checkpoint.*not shown blind"):
        twins.show(vault, pair_id, state=state)
    assert twins.read_pairs(vault)[pair_id].order == ()   # nothing drawn, nothing recorded


def test_show_refuses_while_a_review_marker_is_left_in_a_diff(
    repo: Path, vault: Path, spawned: Spawned,
) -> None:
    pair_id, results = _arms(repo, vault)
    _work(results[0].worktree, "# Review checkpoints: none\n")
    _work(results[1].worktree, "x = 1\n")
    with pytest.raises(twins.TwinsError, match="Review checkpoints"):
        twins.show(vault, pair_id, state=_states())


def test_a_plain_pair_is_shown_as_before_whatever_its_text(
    repo: Path, vault: Path, spawned: Spawned, tmp_path: Path,
) -> None:
    tags = iter(("aaaaaa", "bbbbbb"))
    pair_id, results = twins.dispatch_twins(449, repo_root=repo, fetch=_issue,
                                            vault_root=vault, draw=lambda: next(tags))
    for r in results:
        _work(r.worktree, "x = 1\n")
    state = _states(**{"00000001": _report_transcript(tmp_path, "a", REVIEWED_REPORT)})
    out = twins.show(vault, pair_id, state=state)
    assert review.HEADING in out


def test_the_arm_is_revealed_only_by_prefer(
    repo: Path, vault: Path, spawned: Spawned, monkeypatch, capsys,
) -> None:
    pair_id, results = _arms(repo, vault)
    for r in results:
        _work(r.worktree, "x = 1\n")
    monkeypatch.setattr(twins, "default_vault", lambda: vault)
    monkeypatch.setattr(twins, "_state", _states())

    twins_cmd.cmd_twins(argparse.Namespace(action="show", pair=pair_id, answer=None))
    shown = capsys.readouterr().out
    assert "arm:" not in shown
    twins_cmd.cmd_twins(argparse.Namespace(action="prefer", pair=pair_id, answer="A"))
    revealed = capsys.readouterr().out
    assert "arm: review" in revealed and "arm: control" in revealed


def test_strip_section_stops_at_the_next_heading_of_its_level() -> None:
    text = "a\n## Review checkpoints\n- x\n### sub\n- y\n## Other\nb\n"
    assert review.strip_section(text) == "a\n## Other\nb\n"
    assert review.strip_section("a\n# Review checkpoints\n- x\n") == "a\n"


# --- the command line --------------------------------------------------------


def _args(**kw) -> argparse.Namespace:
    base = dict(issues=[449], contract=None, model=None, effort=None, may=None,
                read_only=False, example=False, dry_run=False, full_profile=False,
                twins=False, review=None, arms=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.parametrize(("kw", "says"), [
    (dict(arms=True), "needs --twins"),
    (dict(review=True, read_only=True), "read-only"),
    (dict(review=True, contract="plan.md", issues=[]), "contract pieces"),
    (dict(review=True, twins=True), "--arms"),
])
def test_the_cli_refuses_combinations_before_anything_spawns(
    kw, says, repo: Path, monkeypatch, spawned: Spawned, capsys,
) -> None:
    monkeypatch.chdir(repo)
    assert dispatch_cmd.cmd_dispatch(_args(**kw)) == 1
    assert says in capsys.readouterr().out
    assert spawned.calls == []


def test_the_config_switch_is_off_by_default_and_the_flag_wins(monkeypatch) -> None:
    from mnemo.core import config

    assert config.DEFAULTS["dispatch"]["review"] is False
    monkeypatch.setattr(config, "load_config", lambda: {"dispatch": {"review": True}})
    assert dispatch_cmd._review_on(None) is True
    assert dispatch_cmd._review_on(False) is False
    monkeypatch.setattr(config, "load_config", lambda: {"dispatch": {}})
    assert dispatch_cmd._review_on(None) is False
    assert dispatch_cmd._review_on(True) is True


def test_the_parser_takes_the_flags_and_the_command() -> None:
    from mnemo.cli.parser import _build_parser

    parser = _build_parser()
    ns = parser.parse_args(["dispatch", "449", "--twins", "--arms"])
    assert ns.arms and ns.twins and ns.review is None
    assert parser.parse_args(["dispatch", "449", "--review"]).review is True
    assert parser.parse_args(["dispatch", "449", "--no-review"]).review is False
    assert parser.parse_args(["review", "record"]).action == "record"


def test_record_help_shows_the_shape_the_prompt_sends_the_child_to(capsys) -> None:
    from mnemo.cli.parser import _build_parser

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["review", "record", "--help"])
    out = capsys.readouterr().out
    assert '"outcome": "accepted" | "rejected"' in out and '"design": "skipped"' in out
