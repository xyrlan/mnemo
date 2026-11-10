"""An advised child is told when to consult its advisor (#832).

#818's first four pairs gave a Sonnet child an Opus advisor and no word about
when to use it, and none of the four called it. So an advised child's prompt
now carries three checkpoints (``dispatch._advisor_clause``), and:

1. without an advisor the prompt is byte-identical, and a Grok child — which
   never has one — never gets the clause;
2. the two arms of a pair now differ in prompt, so each arm's sha256 is
   recorded with the pair;
3. the blind view cuts the account the clause asks for from both reports,
   since it names the arm.
"""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
from pathlib import Path
from typing import Dict, List

import pytest

from mnemo.core import claude_cli, contracts, dispatch, twins
from tests.unit import _git_template

#: The clause's first words, as the child reads them.
OPENING = "You have an advisor: the `advisor` tool"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clause(prompt: str) -> str:
    """The advisor clause *prompt* carries, or ``""``."""
    if OPENING not in prompt:
        return ""
    start = prompt.index(OPENING)
    return prompt[start - 1: prompt.index("\nHow to finish", start)]


# --- the prompt, both ways -----------------------------------------------------


def test_an_advised_prompt_says_when_and_what_right_before_the_closing() -> None:
    prompt = dispatch.build_prompt(734, title="t", body="b", may=("push", "pr"),
                                   advised=True)
    clause = _clause(prompt)

    for checkpoint in ("before settling your plan: after exploring, before your first edit",
                       "after you hit the same error twice",
                       "before declaring the work done: on the finished diff, committed,"):
        assert checkpoint in " ".join(clause.split())
    said = " ".join(clause.split())
    assert "what you decided and why, what you ruled out, and the evidence" in said
    assert "resolve that before you go on" in said
    assert "paragraph that starts `Advisor:`" in said
    assert "how many times you consulted it and what changed because of it" in said
    # After the scope and the refusal licence, before the closing, which still
    # ends the prompt: `for_agent` and the closing tests read it there.
    assert prompt.index("No approach is prescribed") < prompt.index(OPENING)
    assert prompt.endswith(dispatch._closing_clause(("push", "pr")))
    assert all(len(line) <= 78 for line in clause.splitlines())


@pytest.mark.parametrize("build", [
    lambda advised: dispatch.build_prompt(734, title="t", body="b", advised=advised),
    lambda advised: dispatch.build_prompt(734, title="t", body="b", may=("push", "pr"),
                                          advised=advised),
    lambda advised: dispatch.build_prompt(734, title="t", body="b", read_only=True,
                                          advised=advised),
    lambda advised: dispatch.build_prompt(734, title="t", body="b", blind=True,
                                          advised=advised),
    lambda advised: dispatch.build_piece_prompt(
        contracts.Piece(slug="p", files=["a.py"], exposes=["`f()`"]), feature="demo",
        advised=advised),
], ids=["issue", "granted", "read-only", "twin", "piece"])
def test_without_an_advisor_the_prompt_is_the_one_it_always_was(build) -> None:
    """Byte for byte: the advised prompt minus its clause, and no default moved."""
    plain, advised = build(False), build(True)
    clause = _clause(advised)

    assert clause and advised.replace(clause, "", 1) == plain
    assert "advis" not in plain.lower()


def test_the_default_is_no_advisor() -> None:
    assert dispatch.build_prompt(7, title="t", body="b") == \
        dispatch.build_prompt(7, title="t", body="b", advised=False)
    piece = contracts.Piece(slug="p", files=["a.py"], exposes=["`f()`"])
    assert dispatch.build_piece_prompt(piece, feature="d") == \
        dispatch.build_piece_prompt(piece, feature="d", advised=False)


def test_a_read_only_child_is_given_the_checkpoints_of_a_finding() -> None:
    said = " ".join(_clause(dispatch.build_prompt(
        7, title="t", body="b", read_only=True, advised=True)).split())
    assert "before settling your answer" in said
    assert "on your finished finding, before you post it" in said
    assert "first edit" not in said and "committed" not in said


# --- every advised spawn gets it, and only those ---------------------------------


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
    _git_template.copy("advisor-checkpoints-repo", _build_repo, tmp_path, tmp_path_factory)
    return tmp_path / "proj"


def _fetch(issue: int, *, repo_root):
    return dispatch.Issue(number=issue, title="t", body="b")


class Spawned:
    """What the spawn chokepoint was handed, one entry per child."""

    def __init__(self) -> None:
        self.calls: List[Dict] = []

    def __call__(self, prompt, *, cwd, model=None, lean=True, effort=None,
                 read_only=False, settings=None, advisor=None, permission_mode=None):
        self.calls.append({"prompt": prompt, "cwd": Path(cwd), "model": model,
                           "advisor": advisor})
        return f"{len(self.calls):08x}"


@pytest.fixture
def spawned(monkeypatch) -> Spawned:
    rec = Spawned()
    monkeypatch.setattr(dispatch, "spawn_child", rec)
    monkeypatch.setattr(claude_cli, "verify_registered", lambda *a, **k: None)
    return rec


def _told(call: Dict) -> bool:
    return OPENING in call["prompt"]


def test_every_child_handed_an_advisor_is_told_and_no_other(repo: Path, spawned) -> None:
    dispatch.dispatch_all([1, 2], repo_root=repo, fetch=_fetch, model="sonnet",
                          advisor="opus")
    dispatch.dispatch_issue(3, repo_root=repo, fetch=_fetch, model="sonnet")
    dispatch.dispatch_contract(
        contracts.Contract(
            feature="demo", verdict="parallel", path=repo / "c.md",
            pieces=[contracts.Piece(slug=f"p{i}", files=[f"{i}.py"], exposes=["`f()`"], **kw)
                    for i, kw in enumerate([{"advisor": "fable"}, {}])],
        ),
        repo_root=repo, model="sonnet",
    )
    assert [(c["advisor"], _told(c)) for c in spawned.calls] == [
        ("opus", True), ("opus", True), (None, False), ("fable", True), (None, False)]


def test_a_grok_child_never_gets_the_clause(repo: Path, monkeypatch) -> None:
    """Refused with an advisor before anything is read; told nothing without one."""
    with pytest.raises(dispatch.DispatchError, match="Grok children have no advisor"):
        dispatch.dispatch_issue(5, repo_root=repo, agent="grok", advisor="opus",
                                fetch=lambda *a, **k: pytest.fail("read"))

    handed: List[str] = []
    monkeypatch.setattr(dispatch, "check_grok", lambda *a, **k: repo)

    def spawn_grok(target, tree, prompt, **kw):
        handed.append(prompt)
        return dispatch.Dispatched(issue=target, worktree=tree, short_id="0000000g")

    monkeypatch.setattr(dispatch, "_spawn_grok_into", spawn_grok)
    dispatch.dispatch_issue(5, repo_root=repo, fetch=_fetch, agent="grok")
    assert handed and "advis" not in handed[0].lower()


# --- twins: the arms differ in prompt, and the pair says how ----------------------


class _Keep:
    """An rng whose shuffle keeps the order: the advised arm first."""

    def shuffle(self, items):
        return None


def _tags(*tags: str):
    it = iter(tags)
    return lambda: next(it)


def _arms(repo: Path, vault: Path):
    return twins.dispatch_twins(
        832, repo_root=repo, fetch=_fetch, vault_root=vault,
        draw=_tags("aaaaaa", "bbbbbb"), model="sonnet", advisor="opus", rng=_Keep(),
    )


def test_each_arm_s_prompt_sha256_is_recorded_and_they_differ_by_the_clause(
    repo: Path, tmp_path: Path, spawned,
) -> None:
    vault = tmp_path / "vault"
    pair_id, _ = _arms(repo, vault)
    advised, alone = spawned.calls

    assert (advised["advisor"], _told(advised)) == ("opus", True)
    assert (alone["advisor"], _told(alone)) == (None, False)
    assert advised["prompt"].replace(_clause(advised["prompt"]), "", 1) == alone["prompt"]

    line = json.loads(twins.log_path(vault).read_text(encoding="utf-8").splitlines()[0])
    by_tag = {t["tag"]: t for t in line["twins"]}
    assert by_tag["aaaaaa"]["prompt_sha256"] == _sha(advised["prompt"])
    assert by_tag["bbbbbb"]["prompt_sha256"] == _sha(alone["prompt"])
    # The pair's own sha256 stays the prompt without the clause: the alone arm's.
    assert line["prompt_sha256"] == _sha(alone["prompt"])

    pair = twins.read_pairs(vault)[pair_id]
    assert pair.twin("aaaaaa").prompt_sha256 == _sha(advised["prompt"])
    assert pair.twin("bbbbbb").prompt_sha256 == _sha(alone["prompt"])


def test_an_ordinary_pair_records_one_prompt_as_it_always_did(
    repo: Path, tmp_path: Path, spawned,
) -> None:
    vault = tmp_path / "vault"
    pair_id, _ = twins.dispatch_twins(832, repo_root=repo, fetch=_fetch, vault_root=vault,
                                      draw=_tags("aaaaaa", "bbbbbb"), model="opus")
    a, b = spawned.calls
    assert a["prompt"] == b["prompt"] and not _told(a)
    line = json.loads(twins.log_path(vault).read_text(encoding="utf-8").splitlines()[0])
    assert all(set(t) == {"tag", "tree", "branch"} for t in line["twins"])
    assert all(t.prompt_sha256 is None for t in twins.read_pairs(vault)[pair_id].twins)


# --- the blind view does not give the arm away -----------------------------------------


@pytest.mark.parametrize("report", [
    # What the clause asks for: a last paragraph opening on the word.
    "Fixed the flaky test.\n\nAdvisor: consulted 3 times; it flagged the clock, "
    "so I injected one.\nNothing else changed.",
    "Fixed the flaky test.\n\n**Advisor:** consulted twice.\n\nRefused: nothing.",
    # A section under a heading, with its own blocks.
    "## Done\nFixed the flaky test.\n\n## Advisor\n\nConsulted 3 times.\n\n"
    "- plan: the clock\n- done: no change\n\n## Refused\nNothing.",
    # A bare label, its body in the block after it, or right under it.
    "Fixed the flaky test.\n\n**Advisor calls**\n\nThree: plan, error, done.\n\n"
    "Refused: nothing.",
    "Fixed the flaky test.\n\nAdvisor calls:\n- plan: the clock\n- done: no change\n\n"
    "Refused: nothing.",
    # A sentence about its advice in the middle of the report: its line goes.
    "Fixed the flaky test.\nThe advisor pointed at the clock.\nSo the test waits on it.\n\n"
    "Refused: nothing.",
], ids=["paragraph", "bold", "heading", "label", "label-inline", "stray"])
def test_the_account_is_cut_in_each_shape_and_the_rest_kept(report: str) -> None:
    out = twins.blind_report(report)
    assert "advis" not in out.lower()
    assert "Fixed the flaky test" in out
    assert "consulted" not in out.lower() and "plan:" not in out
    if "Refused" in report:
        assert "Refused" in out


def test_only_the_line_goes_outside_the_account() -> None:
    """A report on an issue *about* the advisor names it in a row or a bullet."""
    report = ("Did:\n- added the parser\n- the `advisor:` field\n- fixed the docs\n\n"
              "| file | what |\n| contracts.py | advisor field |\n| docs.md | prose |\n\n"
              "```\nadvisor = parse(x)\nreturn advisor\n```\n\nEnd.")
    out = twins.blind_report(report)
    assert out == ("Did:\n- added the parser\n- fixed the docs\n\n"
                   "| file | what |\n| docs.md | prose |\n\n```\n```\n\nEnd.")


def test_a_report_with_no_advisor_in_it_keeps_its_words() -> None:
    report = "Fixed it.\n\nTests: 12 passed.\n\nRefused: the rename."
    assert twins.blind_report(report) == report


def _line(record: Dict) -> str:
    return json.dumps(record) + "\n"


def _transcript(path: Path, report: str) -> Path:
    path.write_text(
        _line({"type": "user", "message": {"role": "user", "content": "Work on #832"},
               "origin": {"kind": "human"}})
        + _line({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": report}]}}),
        encoding="utf-8",
    )
    return path


def _commit(tree: Path, text: str) -> None:
    (tree / "fix.py").write_text(text, encoding="utf-8")
    for args in (["add", "fix.py"], ["commit", "-qm", "fix"]):
        subprocess.run(["git", *args], cwd=tree, check=True, capture_output=True)


def _states(**paths: Path):
    def state(short_id):
        data = {"state": "done", "tokens": 1, "createdAt": "2026-11-09T10:00:00Z",
                "firstTerminalAt": "2026-11-09T10:10:00Z"}
        if short_id in paths:
            data["linkScanPath"] = str(paths[short_id])
        return data
    return state


ADVISED_REPORT = ("Made the test wait on an injected clock.\n\n"
                  "Advisor: consulted 3 times — before the plan it pointed at the "
                  "clock, so I injected one; on the finished diff, no change.")
ALONE_REPORT = "Made the test wait on an injected clock."


def test_show_cuts_what_names_the_advisor_from_an_arms_pair(
    repo: Path, tmp_path: Path, spawned,
) -> None:
    vault = tmp_path / "vault"
    pair_id, results = _arms(repo, vault)
    for r in results:
        _commit(r.worktree, "clock = 1\n")
    state = _states(**{
        "00000001": _transcript(tmp_path / "advised.jsonl", ADVISED_REPORT),
        "00000002": _transcript(tmp_path / "alone.jsonl", ALONE_REPORT),
    })

    out = twins.show(vault, pair_id, rng=random.Random(0), state=state)

    assert "advis" not in out.lower() and "consulted" not in out
    sections = out.split("===== A =====")[1].split("===== B =====")
    head = f"{'-' * 24} closing report {'-' * 24}\n"
    # Both twins read the same, as far as their reports go.
    assert [s.split(head)[1].split("\n")[0] for s in sections] == [ALONE_REPORT] * 2


def test_an_ordinary_pair_s_report_is_shown_untouched(
    repo: Path, tmp_path: Path, spawned,
) -> None:
    """No arms, nothing to give away: the view of a #449 pair does not move."""
    vault = tmp_path / "vault"
    pair_id, results = twins.dispatch_twins(
        832, repo_root=repo, fetch=_fetch, vault_root=vault,
        draw=_tags("aaaaaa", "bbbbbb"), model="opus",
    )
    for r in results:
        _commit(r.worktree, "x = 1\n")
    report = "Added the `advisor:` field.\n\n\n\nDone."
    state = _states(**{"00000001": _transcript(tmp_path / "a.jsonl", report),
                       "00000002": _transcript(tmp_path / "b.jsonl", report)})

    out = twins.show(vault, pair_id, rng=random.Random(0), state=state)
    assert out.count(report) == 2
