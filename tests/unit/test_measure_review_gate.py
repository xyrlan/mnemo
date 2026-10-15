"""``tools/measure_review_gate.py`` and ``tools/measure_review_pilot.py`` over
synthetic PRs and pairs (#661). Nothing here runs ``claude`` or ``gh``."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_review_gate as gate  # noqa: E402
from tools import measure_review_pilot as pilot  # noqa: E402

from mnemo.core import twins  # noqa: E402


def _run(args, *, cwd) -> str:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                          check=True).stdout


def _commit(root: Path, name: str, text: str) -> str:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _run(["git", "add", "-A"], cwd=root)
    _run(["git", "commit", "-q", "-m", name], cwd=root)
    return _run(["git", "rev-parse", "HEAD"], cwd=root).strip()


@pytest.fixture
def origin(tmp_path: Path):
    root = tmp_path / "origin"
    root.mkdir()
    _run(["git", "init", "-q", "-b", "master"], cwd=root)
    _run(["git", "config", "user.email", "t@example.com"], cwd=root)
    _run(["git", "config", "user.name", "t"], cwd=root)
    base = _commit(root, "src/a.py", "x = 1\n")
    head = _commit(root, "src/a.py", "x = 2  # the PR\n")
    later = _commit(root, "src/a.py", "x = 3  # the later fix\n")
    _commit(root, "FIX.md", "the bug was x = 2\n")
    return root, base, head, later


# --- the no-hindsight repository ---------------------------------------------


def test_the_snapshot_holds_base_and_head_and_nothing_later(origin, tmp_path: Path) -> None:
    root, base, head, later = origin
    snap = tmp_path / "snap"
    new_base, new_head = gate.snapshot(root, base, head, snap)

    assert _run(["git", "rev-list", "--all", "--count"], cwd=snap).strip() == "2"
    assert _run(["git", "for-each-ref", "--format=%(refname)"], cwd=snap).split() == \
        ["refs/heads/main"]
    assert _run(["git", "remote"], cwd=snap).strip() == ""
    for sha in (later, head, base):   # not one original object id is present
        assert subprocess.run(["git", "cat-file", "-e", sha], cwd=str(snap),
                              capture_output=True).returncode != 0
    assert (snap / "src" / "a.py").read_text(encoding="utf-8") == "x = 2  # the PR\n"
    assert not (snap / "FIX.md").exists()
    diff = _run(["git", "diff", new_base, new_head], cwd=snap)
    assert "-x = 1" in diff and "+x = 2  # the PR" in diff and "x = 3" not in diff


def test_a_rerun_rebuilds_the_snapshot_from_scratch(origin, tmp_path: Path) -> None:
    root, base, head, _later = origin
    snap = tmp_path / "snap"
    gate.snapshot(root, base, head, snap)
    (snap / "stray.txt").write_text("left over", encoding="utf-8")
    gate.snapshot(root, base, head, snap)
    assert not (snap / "stray.txt").exists()
    assert _run(["git", "rev-list", "--all", "--count"], cwd=snap).strip() == "2"


# --- the reviewer, as the child's own subagent -------------------------------


def test_the_reviewer_is_the_one_661_shipped_with_hooks_off(tmp_path: Path) -> None:
    # Moved out of src by #682; a read-only subagent on the child's model.
    agent = json.loads(gate.agents_json())[gate.AGENT_NAME]
    assert agent["model"] == "inherit"
    assert not {"Edit", "Write", "NotebookEdit"} & set(agent["tools"])
    argv = gate.reviewer_argv(tmp_path / "agents.json", None)
    assert argv[argv.index("--agent") + 1] == gate.AGENT_NAME
    assert argv[argv.index("--agents") + 1] == str(tmp_path / "agents.json")
    for flag in ("--print", "--restricted", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert json.loads(argv[argv.index("--settings") + 1]) == {"autoMemoryEnabled": False}
    assert "--model" not in argv
    assert gate.reviewer_env()["MNEMO_HOOKS_OFF"] == "1"


def test_review_pr_hands_the_pre_pr_task(origin, tmp_path: Path, monkeypatch) -> None:
    root, base, head, _later = origin
    seen = {}

    def run(argv, **kw):
        seen.update(argv=argv, **kw)
        answer = "ok\n```json\n" + json.dumps({"findings": [{"category": "bug",
                 "file": "src/a.py", "anchor": "src/a.py:1", "finding": "x"}],
                 "suggestions": [], "suggested_issues": []}) + "\n```"
        line = json.dumps({"type": "result", "result": answer, "total_cost_usd": 0.5})
        return subprocess.CompletedProcess(argv, 0, line + "\n", "")

    # The PR's commits as `gh` and `git fetch` would name them; no network.
    monkeypatch.setattr(gate, "pr_commits", lambda source, number, *, gh: {
        "title": "the PR", "base": base, "head": head})
    out = gate.review_pr(root, 7, tmp_path / "work", model=None, run=run)
    snap_base = _run(["git", "rev-list", "--max-parents=0", "HEAD"],
                     cwd=tmp_path / "work" / "pr-7" / "repo").strip()
    assert seen["input"] == gate.PRE_PR_TASK.format(what="the PR", base=snap_base)
    assert Path(seen["cwd"]) == tmp_path / "work" / "pr-7" / "repo"
    assert seen["env"]["MNEMO_HOOKS_OFF"] == "1"
    assert json.loads((tmp_path / "work" / "pr-7" / "agents.json").read_text(encoding="utf-8")) == \
        json.loads(gate.agents_json())
    assert out["parsed"] and out["findings"][0]["anchor"] == "src/a.py:1"
    assert out["cost_usd"] == 0.5


def test_a_tool_call_outside_the_snapshot_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    inside, outside = root / "src" / "a.py", tmp_path / "elsewhere" / "b.py"
    lines = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": str(inside)}},
            {"type": "tool_use", "name": "Read", "input": {"file_path": str(outside)}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "git diff abc HEAD"}},
        ]}},
        {"type": "result", "result": "done", "total_cost_usd": 0.1,
         "usage": {"output_tokens": 42}},
    ]
    out = gate.read_stream("\n".join(json.dumps(x) for x in lines), root)
    assert out["tools"] == ["Read", "Read", "Bash"]
    assert [Path(p) for p in out["escaped"]] == [outside]
    # Shell punctuation and Claude Code's parked tool output are not escapes.
    assert not gate.is_escape(f"{root};", root)
    parked = tmp_path / ".claude" / "projects" / "-x-repo" / "abc" / "tool-results" / "b1.txt"
    assert not gate.is_escape(str(parked), root)
    assert gate.is_escape(str(tmp_path / ".claude" / "sessions"), root)
    assert out["result"] == "done" and out["output_tokens"] == 42


# --- the controls ------------------------------------------------------------

NOW = dt.datetime(2026, 10, 14, tzinfo=dt.timezone.utc)


def _pr(number, merged, *, branch=None, body="", closes=()):
    return {"number": number, "title": f"PR {number}", "body": body,
            "headRefName": branch or f"fix/issue-{number - 1000}",
            "mergedAt": merged, "closingIssuesReferences": [{"number": n} for n in closes]}


def test_a_control_is_an_old_enough_issue_child_nothing_followed_up() -> None:
    merged = [
        _pr(1001, "2026-09-20T00:00:00Z"),                          # ok
        _pr(1002, "2026-09-20T00:00:00Z"),                          # followed up by 1010
        _pr(1003, "2026-10-10T00:00:00Z"),                          # too recent
        _pr(1004, "2026-09-20T00:00:00Z", branch="feat/x/piece"),   # a contract piece
        _pr(1005, "2026-09-20T00:00:00Z"),                          # excluded
        _pr(1006, "2026-09-01T00:00:00Z"),                          # cited after its window
        _pr(1010, "2026-09-25T00:00:00Z", branch="main-fix", body="Fixes the bug #2 left"),
        _pr(1011, "2026-10-12T00:00:00Z", branch="other", body="follows #1006"),
    ]
    drawn, how = gate.select_controls(merged, now=NOW, excluded={1005}, n=12)
    assert drawn == [1001, 1006]
    assert how["dropped"] == {"not_a_child": 3, "excluded": 1, "too_recent": 1,
                              "followed_up": 1}


def test_the_draw_is_reproducible_by_its_seed() -> None:
    merged = [_pr(1000 + i, "2026-09-15T00:00:00Z") for i in range(40)]
    a, _ = gate.select_controls(merged, now=NOW, seed=661, n=12)
    b, _ = gate.select_controls(merged, now=NOW, seed=661, n=12)
    c, _ = gate.select_controls(merged, now=NOW, seed=1, n=12)
    assert a == b and len(a) == 12 and a != c


def test_a_follow_up_cites_the_pr_or_its_issue_as_a_whole_number() -> None:
    pr = _pr(1002, "2026-09-20T00:00:00Z", closes=(77,))
    later = [_pr(2000, "2026-09-21T00:00:00Z", body="see #770"),
             _pr(2001, "2026-09-22T00:00:00Z", body="fixes #77's regression")]
    assert gate.followed_up(pr, later) == [2001]


def test_the_candidates_survived_re_verification_and_are_never_controls() -> None:
    ids = {c["id"] for c in gate.CANDIDATES}
    assert {"227", "405"}.isdisjoint(ids)
    assert {pid for pid, _ in gate.DROPPED} == {"227", "405"}
    assert {n for c in gate.CANDIDATES for n in c["prs"]} <= gate.EXCLUDED


# --- the verdict -------------------------------------------------------------


def _cands(n):
    return [{"id": str(i), "runs": [{"pr": i, "findings": []}]} for i in range(n)]


def _controls(counts):
    return [{"pr": 500 + i, "findings": [{"finding": "f"}] * k} for i, k in enumerate(counts)]


def _labels(caught, control_calls):
    return {"candidates": {str(i): {"caught": i < caught, "why": "read"} for i in range(10)},
            "controls": control_calls}


def test_the_bar_is_a_third_of_the_survivors_and_one_false_finding_per_control() -> None:
    controls = _controls([2] * 12)
    all_false = {str(500 + i): {"0": "false", "1": "false"} for i in range(12)}
    half_false = {str(500 + i): {"0": "false", "1": "real"} for i in range(12)}

    passed = gate.verdict(_cands(10), controls, _labels(4, half_false))
    assert passed["verdict"] == "pass" and passed["needed"] == 4
    assert passed["false_per_control"] == 1.0 and passed["real_on_controls"] == 12

    assert gate.verdict(_cands(10), controls, _labels(3, half_false))["verdict"] == "fail"
    assert gate.verdict(_cands(10), controls, _labels(10, all_false))["verdict"] == "fail"
    assert gate.verdict(_cands(12), _controls([0] * 12), _labels(4, {}))["needed"] == 4


def test_an_unread_finding_leaves_the_verdict_pending() -> None:
    result = gate.verdict(_cands(10), _controls([1]), _labels(10, {}))
    assert result["verdict"] == "pending"
    assert result["unlabelled"] == ["control #500 finding 0"]


def test_the_report_puts_each_finding_next_to_its_fix_and_ends_on_the_verdict() -> None:
    cand = {**gate.CANDIDATES[1], "runs": [{"pr": 205, "parsed": True, "findings": [
        {"category": "bug", "anchor": "src/mnemo/core/dispatch.py:306", "finding": "takes the last token"}]}]}
    labels = {"candidates": {"205": {"caught": True, "why": "same file, same parse"}}}
    result = gate.verdict([cand], [], labels)
    lines = gate.report_lines([cand], [], result, labels, {"seed": 661, "pool": 0, "dropped": {}})
    text = "\n".join(lines)
    assert "fix mechanism: spawn_child takes the last whitespace token" in text
    assert "0. bug src/mnemo/core/dispatch.py:306: takes the last token" in text
    assert "call: same file, same parse" in text
    assert lines[-1] == "VERDICT: fail"     # no controls, so no false-finding rate


# --- the pilot readout -------------------------------------------------------


def _pair(i, choice, reviewed_wins, tokens=(30000, 20000), arms=True):
    """A pair, and its ``{tag: arm}`` as the log records it (``{}`` for a plain one)."""
    review_tag, control_tag = f"r{i:05d}", f"c{i:05d}"
    tw = (twins.Twin(tag=review_tag, tree="t1", branch="b1", short_id="s1"),
          twins.Twin(tag=control_tag, tree="t2", branch="b2", short_id="s2"))
    preferred = "" if choice == "tie" else (review_tag if reviewed_wins else control_tag)
    pair = twins.Pair(pair=f"p{i}", issue=i, repo_root="/r", base="b", twins=tw,
                      order=(review_tag, control_tag), choice=choice, preferred=preferred,
                      metrics={review_tag: {"tokens": tokens[0]},
                               control_tag: {"tokens": tokens[1]}})
    return pair, ({review_tag: "review", control_tag: "control"} if arms else {})


def _readout(made, ledger=()):
    return pilot.readout([p for p, _ in made], {p.pair: a for p, a in made if a},
                         list(ledger))


def test_positive_needs_eighty_percent_of_at_least_six_decided() -> None:
    five_of_six = [_pair(i, "A", reviewed_wins=i != 0) for i in range(6)]
    r = _readout(five_of_six)
    assert (r["decided"], r["review_wins"], r["verdict"]) == (6, 5, "positive")

    four_of_six = [_pair(i, "A", reviewed_wins=i > 1) for i in range(6)]
    assert _readout(four_of_six)["verdict"] == "negative"      # 67%

    five_of_five = [_pair(i, "A", reviewed_wins=True) for i in range(5)]
    assert _readout(five_of_five)["verdict"] == "negative"     # too few decided

    with_ties = five_of_six + [_pair(9, "tie", False), _pair(10, "", False)]
    r = _readout(with_ties)
    assert (r["pairs"], r["answered"], r["decided"], r["ties"]) == (8, 7, 6, 1)
    assert r["tokens_review"] == 8 * 30000 and r["tokens_control"] == 8 * 20000


def test_plain_pairs_are_not_the_pilot_and_ledger_rows_count_per_review_arm() -> None:
    made = [_pair(1, "A", True), _pair(2, "A", True, arms=False)]
    ledger = [{"twin_tag": "r00001", "outcome": "accepted"},
              {"twin_tag": "r00001", "outcome": "rejected"},
              {"twin_tag": "r00001", "outcome": "accepted"},
              {"twin_tag": "c00001", "outcome": "accepted"}]
    r = _readout(made, ledger)
    assert r["pairs"] == 1
    assert (r["findings_accepted"], r["findings_rejected"]) == (2, 1)
    assert r["then"] == "remove the feature; no second round"


def _log(vault: Path, *events) -> None:
    path = twins.log_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_the_arms_and_the_ledger_are_read_from_the_vault_without_src(tmp_path: Path) -> None:
    # #682 took the arms out of twins.read_pairs and the ledger reader out of
    # src; the pilot's log still has both, and the tool reads them itself.
    entries = [{"tag": "aaaaaa", "tree": "/t/a", "branch": "b-a", "arm": "review"},
               {"tag": "bbbbbb", "tree": "/t/b", "branch": "b-b", "arm": "control"}]
    _log(tmp_path,
         {"event": "pair", "pair": "p1", "issue": 7, "repo_root": "/r", "base": "x",
          "arms": True, "twins": entries},
         {"event": "pair", "pair": "p2", "issue": 8, "repo_root": "/r", "base": "x",
          "twins": [{k: v for k, v in e.items() if k != "arm"} for e in entries]},
         {"event": "started", "pair": "p1", "tag": "aaaaaa", "short_id": "s1", "error": ""},
         {"event": "started", "pair": "p1", "tag": "bbbbbb", "short_id": "s2", "error": ""},
         {"event": "shown", "pair": "p1", "order": ["bbbbbb", "aaaaaa"],
          "metrics": {"aaaaaa": {"tokens": 9}, "bbbbbb": {"tokens": 4}}},
         {"event": "prefer", "pair": "p1", "choice": "B"})
    ledger = tmp_path / ".mnemo" / pilot.LEDGER_NAME
    ledger.write_text('{"twin_tag": "aaaaaa", "outcome": "accepted"}\nnot json\n',
                      encoding="utf-8")

    assert pilot.read_arms(tmp_path) == {"p1": {"aaaaaa": "review", "bbbbbb": "control"}}
    assert pilot.read_ledger(tmp_path) == [{"twin_tag": "aaaaaa", "outcome": "accepted"}]
    assert pilot.read_arms(tmp_path / "nowhere") == {}


def test_main_reads_the_review_arm_win_off_the_log(tmp_path: Path, capsys) -> None:
    _log(tmp_path,
         {"event": "pair", "pair": "p1", "issue": 7, "repo_root": "/r", "base": "x",
          "arms": True,
          "twins": [{"tag": "aaaaaa", "tree": "/t/a", "branch": "b-a", "arm": "review"},
                    {"tag": "bbbbbb", "tree": "/t/b", "branch": "b-b", "arm": "control"}]},
         {"event": "started", "pair": "p1", "tag": "aaaaaa", "short_id": "s1", "error": ""},
         {"event": "started", "pair": "p1", "tag": "bbbbbb", "short_id": "s2", "error": ""},
         {"event": "shown", "pair": "p1", "order": ["bbbbbb", "aaaaaa"],
          "metrics": {"aaaaaa": {"tokens": 9}, "bbbbbb": {"tokens": 4}}},
         {"event": "prefer", "pair": "p1", "choice": "B"})   # B is aaaaaa, the review arm
    assert pilot.main(["--vault", str(tmp_path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["answered"], out["decided"], out["review_wins"]) == (1, 1, 1)
    assert (out["tokens_review"], out["tokens_control"]) == (9, 4)
