"""``tools/measure_dispatch_outcomes.py``: what each dispatched child and each
plain ``claude --bg`` job delivered (#556), over synthetic transcripts, a
synthetic vault and a fake ``gh``."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_dispatch_outcomes as tool  # noqa: E402

T0 = 1_790_000_000.0
URL = "https://github.com/o/r/pull/12"
#: A cwd no temp-dir check calls throwaway: it is never created.
TREE = "/Users/you/github/app-wt-7"


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)) + ".123Z"


def _report_ts(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(epoch))


def _user(at: float, content: Any, **extra: Any) -> Dict[str, Any]:
    return {"type": "user", "timestamp": _iso(at), "message": {"content": content}, **extra}


def _assistant(at: float, blocks: List[Dict[str, Any]], *, mid: str = "",
               out: Optional[int] = None) -> Dict[str, Any]:
    message: Dict[str, Any] = {"content": blocks}
    if mid:
        message["id"] = mid
    if out is not None:
        message["usage"] = {"output_tokens": out}
    return {"type": "assistant", "timestamp": _iso(at), "message": message}


def _child(directory: Path, sid: str, *, cwd: str = TREE, kind: str = "bg",
           prompt: str = "Work on issue #7 in this repo: x", pr: Optional[str] = URL,
           wakes: Sequence[float] = (), branch: str = "fix/issue-7",
           end: float = T0 + 600, extra: Sequence[Dict[str, Any]] = ()) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    first = _user(T0, prompt, sessionKind=kind, cwd=cwd, sessionId=sid, gitBranch=branch)
    records: List[Dict[str, Any]] = [first]
    records.append(_assistant(T0 + 100, [{"type": "tool_use", "id": "l", "name": "Bash",
                                          "input": {"command": "gh pr list"}}], mid="m1", out=10))
    records.append(_user(T0 + 110, [{"type": "tool_result", "tool_use_id": "l",
                                     "content": "https://github.com/o/r/pull/99\n"}]))
    if pr:
        records.append(_assistant(T0 + 290, [{"type": "tool_use", "id": "p", "name": "Bash",
                                              "input": {"command": "gh pr create --fill"}}],
                                  mid="m2", out=20))
        records.append(_user(T0 + 300, [{"type": "tool_result", "tool_use_id": "p",
                                         "content": f"{pr}\n"}]))
    records.extend(extra)
    records.append(_assistant(end, [{"type": "text", "text": "report"}], mid="m3", out=5))
    for at in wakes:
        records.append(_user(at, '<mnemo-pr-follow pr="12" events="ci-red">\n…'))
        records.append(_assistant(at + 900, [{"type": "text", "text": "fixed"}], mid=f"w{at}", out=7))
    path = directory / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


class Gh:
    """A fake ``gh``/``git``: PRs by URL, check runs by commit, branches by head."""

    def __init__(self, prs: Dict[str, Dict[str, Any]], checks: Dict[str, List[List[Any]]],
                 branches: Optional[Dict[str, List[Dict[str, str]]]] = None,
                 remote: str = "git@github.com:o/r.git") -> None:
        self.prs, self.checks, self.branches, self.remote = prs, checks, branches or {}, remote
        self.calls: List[Tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str], cwd: Optional[str]) -> Tuple[int, str, str]:
        self.calls.append(tuple(argv))
        if argv[:3] == ["gh", "pr", "view"]:
            pr = self.prs.get(argv[3])
            return (0, json.dumps(pr), "") if pr is not None else (1, "", "not found")
        if argv[:2] == ["gh", "api"]:
            oid = argv[2].split("/commits/")[1].split("/")[0]
            if oid not in self.checks:
                return 1, "", "no"
            return 0, json.dumps(self.checks[oid]), ""
        if argv[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps(self.branches.get(argv[argv.index("--head") + 1], [])), ""
        if argv[:1] == ["git"]:
            return 0, self.remote + "\n", ""
        return 1, "", "unexpected"


def _pr(commits: Sequence[Tuple[float, str]], *, merged: Optional[float] = T0 + 7200) -> Dict[str, Any]:
    return {"state": "MERGED" if merged else "OPEN", "createdAt": _iso(T0 + 300),
            "mergedAt": _iso(merged) if merged else None,
            "commits": [{"oid": oid, "authoredDate": _iso(at)} for at, oid in commits]}


GREEN = [["completed", "success"], ["completed", "skipped"]]
RED = [["completed", "success"], ["completed", "failure"]]


def _vault(tmp_path: Path, *, parents: Sequence[str] = (), reports: Sequence[Dict[str, Any]] = (),
           follow: Optional[Dict[str, Any]] = None) -> Path:
    state = tmp_path / "vault" / ".mnemo"
    state.mkdir(parents=True)
    (state / "dispatch-parents.jsonl").write_text(
        "".join(json.dumps({"short_id": s, "parent_session": "p" * 36}) + "\n" for s in parents),
        encoding="utf-8")
    (state / "child-reports.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in reports), encoding="utf-8")
    (state / "pr-follow.json").write_text(json.dumps({"children": follow or {}}), encoding="utf-8")
    return state.parent


def _jobs(tmp_path: Path, states: Dict[str, Dict[str, Any]]) -> str:
    root = tmp_path / "jobs"
    for sid, state in states.items():
        (root / sid).mkdir(parents=True)
        (root / sid / "state.json").write_text(json.dumps(state), encoding="utf-8")
    root.mkdir(exist_ok=True)
    return str(root)


# --- one transcript ---------------------------------------------------------


def test_a_transcript_reads_its_stops_wakes_turns_and_tokens(tmp_path) -> None:
    extra = [
        _assistant(T0 + 400, [{"type": "tool_use", "id": "q", "name": "AskUserQuestion",
                               "input": {}}], mid="m4", out=3),
        _user(T0 + 410, [{"type": "tool_result", "tool_use_id": "q", "content": "blue"}]),
        _user(T0 + 420, "use the other table"),
        _user(T0 + 430, "<mnemo-resume reason=limit>"),
        _assistant(T0 + 440, [{"type": "tool_use", "id": "r", "name": "mcp__mnemo__read_mnemo_rule",
                               "input": {}}], mid="m4", out=3),
        {"type": "attachment", "timestamp": _iso(T0 + 1),
         "attachment": {"hookEvent": "SessionStart", "stdout": "mnemo://v1 project=app"}},
    ]
    path = _child(tmp_path / "p", "aaaaaaaa-1", wakes=[T0 + 3600], extra=extra)
    row = tool.read_transcript(str(path))
    assert row is not None
    assert row["short_id"] == "aaaaaaaa" and row["bg"] and row["branch"] == "fix/issue-7"
    assert row["created_prs"] == [URL]  # not the #99 `gh pr list` printed
    assert row["first_stop"] == tool._epoch(_iso(T0 + 600))
    assert row["last_turn"] == tool._epoch(_iso(T0 + 4500))
    assert row["wakes"] == 1 and row["resumes"] == 1
    # The typed turn and the answered question; not the opening prompt, the
    # resume or the wake.
    assert row["human_turns"] == 1 and row["answered_questions"] == 1
    # m4 is written twice and counted once.
    assert row["transcript_tokens"] == 10 + 20 + 3 + 5 + 7
    assert row["mnemo_hooks"] == 1 and row["mnemo_mcp_calls"] == 1


def test_a_transcript_without_a_user_record_is_nothing(tmp_path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_text(json.dumps(_assistant(T0, [])) + "\n", encoding="utf-8")
    assert tool.read_transcript(str(path)) is None


# --- the arms ---------------------------------------------------------------


def _row(cwd: str, *, sid: str = "aaaaaaaa", prompt: str = "Work on issue #7",
         pr: Optional[str] = None) -> Dict[str, Any]:
    return {"cwd": cwd, "short_id": sid, "prompt": prompt, "pr": pr}


def test_the_arm_is_read_from_the_parent_link_and_the_tree_s_shape() -> None:
    assert tool.arm_of(_row(TREE), {}) == ("dispatch", "issue")
    assert tool.arm_of(_row("/Users/you/github/app-wt-c-cockpit"), {}) == ("dispatch", "piece")
    assert tool.arm_of(_row("/Users/you/github/app-wt-7-3fa9c1"), {}) == ("dispatch", "twin")
    assert tool.arm_of(_row(TREE, prompt="Investigate issue #7 in this repo"), {}) == \
        ("dispatch", "read-only")
    assert tool.arm_of(_row("/Users/you/github/app"), {"aaaaaaaa": "p"}) == ("dispatch", "other")
    assert tool.arm_of(_row("/Users/you/github/app", pr=URL), {}) == ("plain", "task")
    assert tool.arm_of(_row("/Users/you/github/app"), {}) == ("plain", "no-pr")


def test_a_temp_cwd_is_a_probe_whatever_its_shape(tmp_path) -> None:
    """The suite's live tests dispatch real children into ``…/live-wt-1``."""
    assert tool.arm_of(_row(str(tmp_path / "live-wt-1")), {}) == ("dispatch", "probe")
    assert tool.arm_of(_row(str(tmp_path / "probe")), {}) == ("plain", "probe")


def test_the_pr_comes_from_the_child_s_own_create_first() -> None:
    row = {"created_prs": [URL], "branch": "fix/issue-7", "cwd": TREE, "started": T0}
    assert tool.pick_pr(row, {"pr": "https://github.com/o/r/pull/1"}, None) == (URL, "created")
    row["created_prs"] = []
    assert tool.pick_pr(row, {"pr": URL}, None) == (URL, "pr-follow")
    state = {"children": [{"kind": "pr", "href": URL}]}
    assert tool.pick_pr(row, None, state) == (URL, "state")
    two = {"children": [{"kind": "pr", "href": URL}, {"kind": "pr", "href": URL + "3"}]}
    assert tool.pick_pr(row, None, two) == (None, None)


def test_a_pr_deliver_opened_is_found_by_the_child_s_branch(monkeypatch) -> None:
    monkeypatch.setattr(tool.os.path, "isdir", lambda p: True)
    gh = Gh({}, {}, branches={"fix/issue-7": [
        {"url": "https://github.com/o/r/pull/3", "createdAt": _iso(T0 - 86400)},
        {"url": URL, "createdAt": _iso(T0 + 700)},
    ]})
    row = {"created_prs": [], "branch": "fix/issue-7", "cwd": TREE, "started": T0}
    assert tool.pick_pr(row, None, None, run=gh) == (URL, "branch")
    assert ("git", "-C", TREE, "remote", "get-url", "origin") in gh.calls
    assert tool.pick_pr({**row, "branch": "master"}, None, None, run=gh) == (None, None)


# --- GitHub -----------------------------------------------------------------


def test_check_runs_read_as_one_word() -> None:
    assert tool.verdict([]) == "none"
    assert tool.verdict(GREEN) == "green"
    assert tool.verdict(RED) == "red"
    assert tool.verdict([["in_progress", None], ["completed", "success"]]) == "pending"
    assert tool.verdict([["in_progress", None], ["completed", "failure"]]) == "red"


def test_the_head_at_hand_off_is_the_last_commit_authored_by_the_first_stop() -> None:
    commits = [(T0 + 100, "a"), (T0 + 500, "b"), (T0 + 5000, "c")]
    assert tool.head_at(commits, T0 + 600) == "b"
    assert tool.head_at(commits, T0) is None


def _outcome_row(**over: Any) -> Dict[str, Any]:
    row = {"pr": URL, "repo": "o/r", "first_stop": T0 + 600, "last_turn": T0 + 4500}
    row.update(over)
    return row


def test_red_at_hand_off_fixed_by_the_child_after_its_wake() -> None:
    gh = Gh({URL: _pr([(T0 + 200, "a"), (T0 + 4000, "b")])}, {"a": RED, "b": GREEN})
    out = tool.outcome(_outcome_row(), run=gh)
    assert out["ci_handoff"] == "red" and out["ci_end"] == "green"
    assert out["red_fixed_by"] == "child"
    assert out["merged"] and out["hours_to_merge"] == 6900 / 3600


def test_red_at_hand_off_fixed_by_someone_else() -> None:
    gh = Gh({URL: _pr([(T0 + 200, "a"), (T0 + 9000, "b")])}, {"a": RED, "b": GREEN})
    out = tool.outcome(_outcome_row(), run=gh)
    assert out["commits"]["after_child"] == 1
    assert out["red_fixed_by"] == "someone-else"


def test_green_at_hand_off_reads_one_commit_and_fixes_nothing() -> None:
    gh = Gh({URL: _pr([(T0 + 200, "a")], merged=None)}, {"a": GREEN})
    out = tool.outcome(_outcome_row(), run=gh)
    assert out["ci_handoff"] == out["ci_end"] == "green"
    assert out["red_fixed_by"] is None and not out["merged"] and out["hours_to_merge"] is None
    assert sum(1 for c in gh.calls if c[:2] == ("gh", "api")) == 1


def test_an_unreadable_pr_has_no_outcome() -> None:
    assert tool.outcome(_outcome_row(), run=Gh({}, {})) is None


def test_the_cache_answers_a_second_run_without_gh(tmp_path) -> None:
    gh = Gh({URL: _pr([(T0, "a")])}, {})
    path = str(tmp_path / "cache.json")
    first = tool.cached(gh, path)(["gh", "pr", "view", URL], None)
    again = tool.cached(Gh({}, {}), path)(["gh", "pr", "view", URL], None)
    assert first == again and first[0] == 0


# --- the notice -------------------------------------------------------------


def _reports(*rows: Tuple[str, float]) -> List[Dict[str, Any]]:
    return [{"event": e, "_at": at} for e, at in rows]


def test_the_parent_s_notice_for_the_first_stop() -> None:
    row = {"first_stop": T0 + 600}
    kw: Dict[str, Any] = {"log_start": T0, "has_parent": True, "state": None}
    assert tool.notice(row, _reports(("spawned", T0 + 601), ("finished", T0 + 603)), **kw) == "told"
    assert tool.notice(row, _reports(("finished", T0 + 900)), **kw) == "late"
    assert tool.notice(row, _reports(("spawned", T0 + 601)), **kw) == "lost"
    assert tool.notice(row, _reports(("finished", T0 + 100)), **kw) == "silent"
    assert tool.notice(row, [], **{**kw, "log_start": T0 + 700}) == "before-log"
    assert tool.notice(row, [], **{**kw, "state": {"state": "done"}}) == "not-stopped"
    assert tool.notice(row, [], **{**kw, "has_parent": False}) is None


def test_the_first_report_s_state_is_the_first_finished_row_after_the_stop() -> None:
    rows = [{"event": "finished", "state": "ci-running", "_at": T0 + 100},
            {"event": "finished", "state": "ci-red", "_at": T0 + 602},
            {"event": "finished", "state": "ready", "_at": T0 + 9000}]
    assert tool.first_report_state({"first_stop": T0 + 600}, rows) == "ci-red"


# --- end to end -------------------------------------------------------------


def test_gather_measures_both_arms_and_counts_a_pr_once(tmp_path) -> None:
    projects = tmp_path / "projects"
    _child(projects / "-Users-you-github-app-wt-7", "aaaaaaaa-1", wakes=[T0 + 3600])
    # A first dispatch of the same issue that named the same PR: superseded.
    _child(projects / "-Users-you-github-app-wt-7", "bbbbbbbb-1", end=T0 + 500)
    # Still running: left out.
    _child(projects / "-Users-you-github-app-wt-8", "cccccccc-1", pr=None)
    # A plain --bg job with a PR of its own, and an interactive session.
    plain = "https://github.com/o/r/pull/20"
    _child(projects / "-Users-you-github-app", "dddddddd-1", cwd="/Users/you/github/app",
           prompt="fix the changelog", pr=plain)
    _child(projects / "-Users-you-github-app", "eeeeeeee-1", cwd="/Users/you/github/app",
           kind="", pr=None)
    vault = _vault(
        tmp_path, parents=["aaaaaaaa", "bbbbbbbb"],
        reports=[{"ts": _report_ts(T0 - 10), "short_id": "zzzzzzzz", "event": "spawned"},
                 {"ts": _report_ts(T0 + 601), "short_id": "aaaaaaaa", "event": "finished",
                  "state": "ci-red", "pr": 12}],
        follow={"aaaaaaaa": {"pr": URL, "attempts": [{"at": T0 + 3600, "events": ["ci-red"]}]}},
    )
    jobs = _jobs(tmp_path, {"aaaaaaaa": {"state": "stopped", "tokens": 1234},
                            "cccccccc": {"state": "working"}})
    gh = Gh({URL: _pr([(T0 + 200, "a"), (T0 + 4000, "b")]),
             plain: _pr([(T0 + 250, "p")], merged=None)},
            {"a": RED, "b": GREEN, "p": []})
    rows = tool.gather(str(projects), jobs=jobs, vault=vault, run=gh)
    by_id = {r["short_id"]: r for r in rows}
    assert set(by_id) == {"aaaaaaaa", "bbbbbbbb", "dddddddd"}
    a = by_id["aaaaaaaa"]
    assert (a["arm"], a["kind"], a["pr"], a["pr_source"]) == ("dispatch", "issue", URL, "created")
    assert a["tokens"] == 1234 and a["tokens_source"] == "state"
    assert a["woken_red"] and a["notice"] == "told" and a["first_report"] == "ci-red"
    assert a["gh"]["red_fixed_by"] == "child"
    assert by_id["bbbbbbbb"]["pr"] is None and by_id["bbbbbbbb"]["pr_source"] == "superseded"
    d = by_id["dddddddd"]
    assert (d["arm"], d["kind"], d["notice"]) == ("plain", "task", None)
    assert d["tokens_source"] == "transcript" and d["gh"]["ci_handoff"] == "none"

    report = tool.measure(rows)
    s = report["dispatch"]
    assert (s["jobs"], s["with_pr"], s["merged"], s["merged_no_commit_after_child"]) == (2, 1, 1, 1)
    assert s["ci"]["ci_handoff"] == {"red": 1} and s["red_fixed_by"] == {"child": 1}
    assert s["woken_for_pr"] == 1 and s["woken_red"] == 1
    assert report["plain"]["with_pr"] == 1 and report["plain"]["merged"] == 0
    assert set(report["groups"]) == {"dispatch/issue", "plain/task"}
    text = tool.format_report(report, rows, listing=True)
    assert "dispatch/issue: 2 job(s)" in text and "plain/task: 1 job(s)" in text


def test_gather_without_gh_reads_nothing_from_github(tmp_path) -> None:
    projects = tmp_path / "projects"
    _child(projects / "-Users-you-github-app-wt-7", "aaaaaaaa-1")
    rows = tool.gather(str(projects), jobs=_jobs(tmp_path, {}), vault=_vault(tmp_path), run=None)
    assert rows[0]["pr"] == URL and rows[0]["gh"] is None
    assert tool.measure(rows)["dispatch"]["pr_read"] == 0


def test_since_and_until_bound_the_start(tmp_path) -> None:
    projects = tmp_path / "projects"
    _child(projects / "-Users-you-github-app-wt-7", "aaaaaaaa-1")
    kw: Dict[str, Any] = {"jobs": _jobs(tmp_path, {}), "vault": _vault(tmp_path), "run": None}
    assert len(tool.gather(str(projects), since=T0 - 1, **kw)) == 1
    assert tool.gather(str(projects), since=T0 + 1, **kw) == []
    assert tool.gather(str(projects), until=T0, **kw) == []


# --- private names ----------------------------------------------------------


def test_private_names_are_replaced_by_their_alias(tmp_path) -> None:
    path = tmp_path / "names.tsv"
    path.write_text("secretapp\trepo-a\nsecretapp-web\trepo-b\nlonely\n", encoding="utf-8")
    aliases = tool.load_aliases(path)
    text = "https://github.com/me/secretapp-web/pull/1 and secretapp and Lonely"
    assert tool.redact(text, aliases) == \
        "https://github.com/me/repo-b/pull/1 and repo-a and a-private-repo"
    assert tool.load_aliases(tmp_path / "missing.tsv") == []


# --- the study's primary outcome and size -----------------------------------


def _handed(**over: Any) -> Dict[str, Any]:
    row = {"pr": URL, "human_turns": 0, "answered_questions": 0,
           "gh": {"ci_end": "green", "commits": {"after_child": 0}}}
    row.update(over)
    return row


def test_hands_off_needs_a_green_pr_nobody_else_touched_and_no_person() -> None:
    assert tool.hands_off(_handed())
    assert not tool.hands_off(_handed(pr=None))
    assert not tool.hands_off(_handed(gh=None))
    assert not tool.hands_off(_handed(gh={"ci_end": "red", "commits": {"after_child": 0}}))
    assert not tool.hands_off(_handed(gh={"ci_end": "green", "commits": {"after_child": 1}}))
    assert not tool.hands_off(_handed(answered_questions=1))


def test_mcnemar_pairs_match_connor_by_hand() -> None:
    """0.8 against 0.6: discordance 0.44, difference 0.2 →
    (1.96·√0.44 + 0.8416·√0.40)² / 0.04 = 83.9."""
    assert tool.mcnemar_pairs(0.8, 0.6) == 84
    assert tool.mcnemar_pairs(0.5, 0.5) is None
    # A smaller difference always costs more pairs.
    sizes = [r["clean_pairs"] for r in tool.sizing(0.8)]
    assert sizes == sorted(sizes)


def test_sizing_inflates_for_the_pairs_a_leak_leaves_out() -> None:
    (first, *_) = tool.sizing(0.8, excluded=0.5)
    assert first["pairs_to_run"] == 2 * first["clean_pairs"]
    assert [r["difference"] for r in tool.sizing(0.12)] == [0.10]
