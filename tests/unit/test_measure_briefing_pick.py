"""``tools/measure_briefing_pick.py`` over synthetic transcripts, briefings and raters (#534)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_briefing_pick as tool  # noqa: E402

R1, R2 = tool.RATERS


def _ev(ts, **kw):
    ev = {"timestamp": ts, "cwd": kw.pop("cwd", "/w/app"), "sessionId": kw.pop("sid", "s")}
    ev.update(kw)
    return ev


def _transcript(path: Path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _briefing(vault: Path, project: str, sid: str, body: str, mtime: float, date: str = "2026-09-20",
              extra: str = ""):
    d = vault / "bots" / project / "briefings" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".md")
    p.write_text("---\ntype: briefing\nsession_id: %s\ndate: %s\n%s---\n\n%s\n" % (sid, date, extra, body),
                 encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def _cand(cid, rank, body="", hook_first=False, **signals):
    return {"id": cid, "rank": rank, "signals": signals, "hook_first": hook_first, "body": body}


# --- transcripts ----------------------------------------------------------------------------

def test_session_facts_read_cwd_branches_and_source():
    events = [
        {"type": "attachment", "attachment": {"type": "hook_additional_context",
                                               "hookName": "SessionStart:clear", "content": ["x"]}},
        _ev("2026-09-20T10:00:00Z", gitBranch="master", type="user"),
        _ev("2026-09-20T10:05:00Z", gitBranch="fix/issue-12", type="assistant"),
    ]
    facts = tool.session_facts(events)
    assert facts == {"cwd": "/w/app", "branch": "master", "branch_end": "fix/issue-12", "source": "clear"}


def test_first_prompt_is_what_the_developer_typed():
    prompts = [{"text": "Base directory for this skill: /x"}, {"text": "  "}, {"text": "ok"}, {"text": "more"}]
    assert tool.first_prompt(prompts) == "ok"
    assert tool.first_prompt([]) == ""


def test_issue_and_trunk_and_worktree(tmp_path):
    assert tool.issue_of("fix/issue-534") == "534"
    assert tool.issue_of("feat/issue_12-thing") == "12"
    assert tool.issue_of("feature/relaunch-e4") == ""
    assert not tool.is_task_branch("dev") and not tool.is_task_branch("main") and not tool.is_task_branch("")
    assert tool.is_task_branch("fix/texto-348")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /x/.git/worktrees/wt\n", encoding="utf-8")
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    assert tool.is_linked_worktree(str(wt)) and not tool.is_linked_worktree(str(main))
    assert tool.is_linked_worktree("/gone/github/app-wt-issue-9")
    assert not tool.is_linked_worktree("/gone/github/app")


# --- candidates and signals -----------------------------------------------------------------

def test_signals_need_a_task_branch_and_the_same_cwd():
    session = {"cwd": "/w/app", "branch": "fix/issue-7", "start": 10000.0, "worktree": True}
    same = {"cwd": "/w/app", "branch": "fix/issue-7", "mtime": 10000.0 - 600}
    assert tool.signals(session, same) == {"issue": True, "branch": True, "worktree": True, "cwd_gap": 10.0}
    trunk = dict(session, branch="master", worktree=False)
    assert tool.signals(trunk, dict(same, branch="master")) == {"cwd_gap": 10.0}
    assert tool.signals(trunk, dict(same, cwd="/w/other")) == {}
    renamed = dict(same, branch="feat/issue-7-again", cwd="/w/other")
    assert tool.signals(session, renamed) == {"issue": True}


def test_candidates_are_what_existed_at_start_pool_plus_newest_match():
    session = {"id": "me", "cwd": "/w/app", "branch": "fix/x", "start": 1000.0, "worktree": False}
    briefings = [{"id": "b%02d" % i, "mtime": 900.0 - i, "date": "2026-09-20", "cwd": "/w/app",
                  "branch": "master", "body": "b%d" % i} for i in range(15)]
    briefings[12]["branch"] = briefings[13]["branch"] = "fix/x"      # two older matches past the pool
    briefings.append({"id": "later", "mtime": 1500.0, "date": "2026-09-21", "cwd": "/w/app", "branch": "fix/x",
                      "body": "written after"})
    briefings.append({"id": "me", "mtime": 10.0, "date": "2026-09-20", "cwd": "/w/app", "branch": "fix/x",
                      "body": "its own"})
    cands = tool.candidates_for(session, briefings)
    ids = [c["id"] for c in cands]
    assert ids[:10] == ["b%02d" % i for i in range(10)]
    assert "b12" in ids and "b13" not in ids      # a signal picks its newest match only
    assert "later" not in ids and "me" not in ids
    # The hook's sort: date, then session id — not mtime.
    assert [c["id"] for c in cands if c["hook_first"]] == ["b14"]
    assert "b14" in ids


def test_hook_order_breaks_a_date_by_session_id_not_by_time():
    older = {"id": "zzz", "date": "2026-09-20", "mtime": 1.0}
    newer = {"id": "aaa", "date": "2026-09-20", "mtime": 2.0}
    assert max([older, newer], key=tool.hook_order) is older


# --- the raters -----------------------------------------------------------------------------

def _unit(uid="u1", cands=None, **kw):
    u = {"id": uid, "prompts": ["fix the invoice rounding bug"], "final": "Rounding fixed.",
         "candidates": cands if cands is not None else [_cand("c%d" % i, i, body="note %d" % i) for i in range(4)]}
    u.update(kw)
    return u


def test_rater_prompt_is_shuffled_blind_and_seeded():
    u = _unit()
    order = tool.letters_for(u)
    assert sorted(order) == ["c0", "c1", "c2", "c3"] and order == tool.letters_for(_unit())
    prompt = tool.rater_prompt(u)
    assert "fix the invoice rounding" in prompt and "Rounding fixed." in prompt
    for letter, cid in zip("ABCD", order):
        assert "### Note %s\nnote %s" % (letter, cid[1:]) in prompt
    assert "mnemo" not in prompt.lower() and "rank" not in prompt and "2026" not in prompt


def test_parse_answer_maps_letters_to_ids():
    order = ["c2", "c0", "c1"]
    assert tool.parse_answer('{"about": ["b", "A"], "best": "A", "why": "x"}', order) == {
        "about": ["c0", "c2"], "best": "c2"}
    assert tool.parse_answer('```json\n{"about": [], "best": null}\n```', order) == {"about": [], "best": None}
    assert tool.parse_answer('{"about": [], "best": "C"}', order) == {"about": ["c1"], "best": "c1"}
    assert tool.parse_answer('{"about": ["Z"]}', order) is None
    assert tool.parse_answer('{"about": "A"}', order) is None
    assert tool.parse_answer("nope", order) is None


def test_right_set_is_what_both_raters_listed():
    labels = {R1: {"u": {"about": ["a", "b"]}, "v": {"about": []}}, R2: {"u": {"about": ["b", "c"]}}}
    assert tool.right_set("u", labels) == {"b"}
    assert tool.right_set("v", labels) is None


def test_label_sends_pending_calls_and_caches_answers(tmp_path, monkeypatch):
    units = [_unit("u1"), _unit("u2")]

    class Resp:
        def __init__(self, text):
            self.text, self.total_cost_usd, self.input_tokens, self.output_tokens = text, 0.01, 10, 5

    asked = []

    def provider(prompt, system, model, timeout):
        asked.append(model)
        assert os.getcwd() != str(tmp_path)
        return Resp('{"about": ["A"], "best": "A"}')

    labels = tool.label(units, tmp_path, True, 1, 0.0, None, provider=provider)
    assert len(asked) == 4 and set(labels) == {R1, R2}
    first = tool.letters_for(units[0])[0]
    assert labels[R1]["u1"] == {"about": [first], "best": first}
    again = tool.label(units, tmp_path, True, 1, 0.0, None, provider=provider)
    assert len(asked) == 4 and again == labels            # a rerun resumes, sends nothing


# --- selectors ------------------------------------------------------------------------------

def test_pick_signals_trusts_the_strongest_enabled_signal_then_the_newest():
    u = _unit(cands=[
        _cand("new", 0, cwd_gap=3.0),
        _cand("br", 1, branch=True, cwd_gap=50.0),
        _cand("iss", 4, issue=True),
    ])
    assert tool.pick_signals(u, {"signals": ["issue", "branch", "cwd_gap"], "gap": 5}) == ("iss", "issue")
    assert tool.pick_signals(u, {"signals": ["branch", "cwd_gap"], "gap": 5}) == ("br", "branch")
    assert tool.pick_signals(u, {"signals": ["cwd_gap"], "gap": 5}) == ("new", "cwd_gap")
    assert tool.pick_signals(u, {"signals": ["cwd_gap"], "gap": 2}) is None
    assert tool.pick_signals(u, {"signals": []}) is None


def test_pick_lexical_gates_on_floor_ratio_and_overlap():
    u = {"lexical": [("a", 10.0, 3), ("b", 4.0, 1)], "query_len": 5}
    assert tool.pick_lexical(u, {"floor": 5, "ratio": 2, "overlap": 3}) == ("a", 10.0)
    assert tool.pick_lexical(u, {"floor": 11, "ratio": 1, "overlap": 1}) is None
    assert tool.pick_lexical(u, {"floor": 0, "ratio": 3, "overlap": 1}) is None
    assert tool.pick_lexical(u, {"floor": 0, "ratio": 1, "overlap": 4}) is None
    short = {"lexical": [("a", 10.0, 1)], "query_len": 1}
    assert tool.pick_lexical(short, {"floor": 0, "ratio": 1, "overlap": 4}) == ("a", 10.0)
    assert tool.pick_lexical({"lexical": []}, {}) is None


def test_lexical_scores_rank_the_named_briefing_over_the_ten_newest():
    cands = [_cand("c%d" % i, i, body="# Briefing\nWorked on the dashboard colours and css tokens.")
             for i in range(12)]
    cands[3]["body"] = "# Briefing\nFixed invoice rounding in the billing export; totals now match."
    cands[11]["body"] = "# Briefing\ninvoice rounding invoice rounding billing"   # past the pool
    scores, qlen = tool.lexical_scores("the invoice rounding is off again in billing", cands)
    assert scores[0][0] == "c3" and scores[0][2] == 3 and qlen == 5
    assert all(cid != "c11" for cid, _, _ in scores)
    assert tool.lexical_scores("", cands) == ([], 0)


def test_combined_is_signal_then_lexical_then_none():
    cfg = {"signal": {"signals": ["branch"], "gap": 0}, "lexical": {"floor": 5, "ratio": 1, "overlap": 1}}
    both = _unit(cands=[_cand("br", 2, branch=True)], lexical=[("lx", 9.0, 2)], query_len=2)
    assert tool.pick_combined(both, cfg) == ("br", "signal:branch")
    lex = _unit(cands=[_cand("x", 0)], lexical=[("lx", 9.0, 2)], query_len=2)
    assert tool.pick_combined(lex, cfg) == ("lx", "lexical")
    none = _unit(cands=[_cand("x", 0)], lexical=[("lx", 1.0, 2)], query_len=2)
    assert tool.pick_combined(none, cfg) is None


# --- scoring and the protocol ---------------------------------------------------------------

def test_score_counts_precision_over_picks_and_coverage_over_sessions_with_a_right_one():
    units = [_unit("a"), _unit("b"), _unit("c"), _unit("d")]
    truth = {"a": {"c0"}, "b": {"c1"}, "c": set(), "d": {"c0"}}
    picks = {"a": "c0", "b": "c0", "c": "c3", "d": None}
    s = tool.score(units, truth, lambda u: picks[u["id"]])
    assert (s["injected"], s["right"], s["with_right"]) == (3, 1, 3)
    assert s["precision"] == pytest.approx(1 / 3) and s["coverage"] == pytest.approx(1 / 3)
    assert tool.score([], truth, lambda u: None)["precision"] is None


def test_tune_finds_the_precise_signal_and_reads_only_what_it_is_given():
    units, truth = [], {}
    for i in range(40):
        right = "r%d" % i
        cands = [_cand("n%d" % i, 0, cwd_gap=1.0), _cand(right, 3, branch=(i % 2 == 0))]
        units.append(_unit("u%d" % i, cands=cands, lexical=[], query_len=0))
        truth["u%d" % i] = {right} if i % 4 != 3 else set()
    tuned = tool.tune(units, truth)
    assert tuned["met_bar_on_dev"]
    assert "branch" in tuned["config"]["signal"]["signals"]
    assert "cwd_gap" not in tuned["config"]["signal"]["signals"]      # it would inject the wrong one
    assert tuned["dev"]["precision"] == 1.0


def test_the_split_is_by_session_and_stable():
    ids = ["s%d" % i for i in range(400)]
    dev = [s for s in ids if tool.is_dev(s)]
    assert 150 < len(dev) < 250 and dev == [s for s in ids if tool.is_dev(s)]


def test_verdict_names_the_failing_half_of_the_bar():
    ok, _ = tool.verdict({"precision": 0.9, "coverage": 0.7})
    assert ok
    ok, why = tool.verdict({"precision": 0.9, "coverage": 0.2})
    assert not ok and "coverage" in why and "precision" not in why
    ok, why = tool.verdict({"precision": None, "coverage": None})
    assert not ok and "precision" in why


def test_jev_qualifies_only_on_the_529_report(tmp_path):
    assert tool.jev_qualified(tmp_path) == (False, "no #529 report")
    rows = {"rows": [{"question": "briefing", "status": "pending"}]}
    (tmp_path / "report.json").write_text(json.dumps(rows), encoding="utf-8")
    assert tool.jev_qualified(tmp_path) == (False, "pending")


# --- building the set from synthetic transcripts and briefings ------------------------------

def test_build_units_end_to_end(tmp_path, monkeypatch):
    vault, projects, home = tmp_path / "vault", tmp_path / "projects", tmp_path / "claude"
    home.mkdir()
    t0 = 1790000000.0
    # The writer of the right briefing: branch fix/issue-9 at its end.
    _transcript(projects / "p" / "w1.jsonl", [
        _ev("2026-09-20T09:00:00Z", sid="w1", gitBranch="master", type="user",
            message={"role": "user", "content": "start"}),
        _ev("2026-09-20T09:30:00Z", sid="w1", gitBranch="fix/issue-9", type="assistant",
            message={"content": [{"type": "text", "text": "done"}]}),
    ])
    _briefing(vault, "app", "w1", "# Briefing\nInvoice rounding half done on fix/issue-9.", t0 - 7200,
              date="2026-09-20")
    _briefing(vault, "app", "w2", "# Briefing\nDashboard colours.", t0 - 600, date="2026-09-20",
              extra="cwd: /w/app\nbranch: master\n")
    me = projects / "p" / "me.jsonl"
    _transcript(me, [
        {"type": "attachment", "attachment": {"type": "hook_additional_context",
                                               "hookName": "SessionStart:startup",
                                               "content": ["mnemo://v1\n[last-briefing session=w2]\n# Briefing\n"
                                                           "Dashboard colours.\n[/last-briefing]"]}},
        _ev("2026-09-20T12:00:00Z", sid="me", gitBranch="fix/issue-9", type="user",
            message={"role": "user", "content": "finish the invoice rounding work"}),
        _ev("2026-09-20T12:10:00Z", sid="me", gitBranch="fix/issue-9", type="assistant",
            message={"content": [{"type": "text", "text": "Rounding finished."}]}),
    ])
    sessions = {"me": {"path": str(me), "project": "app", "cwd": "/w/app", "start": t0},
                "w1": {"path": str(projects / "p" / "w1.jsonl"), "project": "app", "cwd": "/w/app",
                       "start": t0 - 9000}}
    monkeypatch.setattr(tool.mpr, "collect_sessions", lambda *a: sessions)
    built = tool.build_units(vault, projects, home, "2026-09-01")
    assert built["counts"]["units"] == 1 and built["counts"]["no_typed"] == 1
    u = built["units"][0]
    assert u["id"] == "me" and u["branch"] == "fix/issue-9"
    by_id = {c["id"]: c for c in u["candidates"]}
    assert set(by_id) == {"w1", "w2"}
    assert by_id["w1"]["signals"].get("issue") and by_id["w1"]["signals"].get("branch")
    assert not by_id["w2"]["signals"].get("branch")                 # frontmatter cwd/branch read
    assert by_id["w2"]["rank"] == 0 and "Dashboard" in u["handed_head"]
    assert u["first_prompt"] == "finish the invoice rounding work" and u["lexical"][0][0] == "w1"
    lock = {"signal": {"signals": ["issue"], "gap": 0}, "lexical": {"floor": 1e9}}
    assert tool.pick_combined(u, lock) == ("w1", "signal:issue")


def test_diagnostics_bound_what_each_layer_can_reach():
    units = [
        _unit("a", cands=[_cand("x", 0, cwd_gap=2.0), _cand("r", 1)], lexical=[("r", 9.0, 2)]),
        _unit("b", cands=[_cand("y", 0, branch=True), _cand("r2", 11)], lexical=[("y", 9.0, 2)]),
        _unit("c", cands=[_cand("z", 0)], lexical=[]),
    ]
    truth = {"a": {"r"}, "b": {"r2"}, "c": set()}
    d = tool.diagnostics(units, truth)
    assert (d["with_right"], d["right_is_latest"], d["right_in_pool"]) == (2, 0, 1)
    assert (d["lexical_top1_right"], d["lexical_top3_right"]) == (1, 1)
    assert d["signals"]["branch"] == {"picks": 1, "right": 0}
    assert d["signals"]["cwd_gap<=5"] == {"picks": 1, "right": 0}
