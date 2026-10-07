"""``tools/measure_action_reviewer.py`` over synthetic transcripts, vaults and
#519 caches whose right answers are known by construction (#599).

No model and no network. Pinned: the turn the correction answered is the work
between the two user turns; the pool holds only rules that existed then; both
arms rank through the reflex's own ``decide``; the primary reading never sees
a hindsight target; recall is counted at the candidate stage and after the
judge; the threshold comes from dev and the numbers from test; the paired CI
is honest at small n; and the dry run sends nothing.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_action_reviewer as tool  # noqa: E402

from mnemo.core import llm  # noqa: E402
from mnemo.core.reflex import index as reflex_index  # noqa: E402

CWD = "/Users/you/github/app"


def _user(text, ts="2026-09-21T10:00:00Z"):
    return {"type": "user", "timestamp": ts, "cwd": CWD, "entrypoint": "cli",
            "message": {"role": "user", "content": text}}


def _result(ts="2026-09-21T10:00:00Z"):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t",
                                                     "content": "ok"}]}}


def _agent(*blocks, ts="2026-09-21T10:00:00Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": list(blocks)}}


def _text(t):
    return {"type": "text", "text": t}


def _call(name, **args):
    return {"type": "tool_use", "id": "t", "name": name, "input": args}


def _write_rule(vault, slug, body, *, sources):
    path = vault / "shared" / "feedback" / (slug + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nname: %s\ndescription: d\nstability: stable\nsources:\n%s---\n%s\n"
                    % (slug, "".join("  - %s\n" % s for s in sources), body), encoding="utf-8")


def _src(sid):
    return "bots/app/briefings/sessions/%s.md" % sid


# --- the turns ---------------------------------------------------------------------------

def test_turn_work_is_the_work_between_two_user_turns():
    events = [
        _user("set up the build"),
        _agent(_text("Looking."), _call("Read", file_path="/r/a.py")),
        _result(),
        _agent(_call("Bash", command="npm install"), _text("Installed with npm.")),
        _result(),
        _user("never use npm here, always yarn"),
        _agent(_call("Edit", file_path="/r/b.py", old_string="x", new_string="y = 1")),
    ]
    work = tool.turn_work(events)
    assert [w["index"] for w in work] == [0, 1, 2]
    assert work[0] == {"index": 0, "prompt": "", "calls": [], "final": "", "reply": "set up the build"}
    assert work[1]["prompt"] == "set up the build"
    assert work[1]["reply"] == "never use npm here, always yarn"
    assert [n for n, _ in work[1]["calls"]] == ["Read", "Bash"]
    assert work[1]["final"] == "Installed with npm."
    # the work after the last user turn has no reply
    assert work[2]["reply"] is None and work[2]["prompt"] == "never use npm here, always yarn"


def test_call_summary_keeps_what_was_touched_and_written():
    assert tool.call_summary("Bash", {"command": "npm  install\n--save"}) == "Bash npm install --save"
    assert tool.call_summary("Edit", {"file_path": "/r/b.py", "new_string": "y = 1"}) == "Edit /r/b.py: y = 1"
    multi = tool.call_summary("MultiEdit", {"file_path": "/r/c.py",
                                            "edits": [{"new_string": "a"}, {"new_string": "b"}]})
    assert multi == "MultiEdit /r/c.py: a b"
    assert tool.call_summary("Write", {"file_path": "/r/d.md", "content": "hi"}) == "Write /r/d.md: hi"
    assert len(tool.call_summary("Bash", {"command": "x" * 1000})) == tool.CALL_CHARS + 1


def test_an_edit_turn_has_an_edit_or_a_command_and_reads_do_not_count():
    assert tool.is_edit_turn({"calls": [("Read", ""), ("Bash", "")]})
    assert tool.is_edit_turn({"calls": [("Write", "")]})
    assert not tool.is_edit_turn({"calls": [("Read", ""), ("Grep", "")]})
    assert not tool.is_edit_turn({"calls": []})


def test_action_text_is_the_calls_then_the_final_message_within_the_cap():
    work = {"calls": [("Bash", "Bash npm install"), ("Edit", "Edit /r/b.py: y")], "final": "Done."}
    assert tool.action_text(work) == "Bash npm install\nEdit /r/b.py: y\nDone."
    capped = tool.action_text({"calls": [("Bash", "x" * 500)], "final": "Done."}, limit=100)
    assert capped.endswith("Done.") and len(capped) <= 100


# --- the pools -----------------------------------------------------------------------------

@pytest.fixture
def rules_vault(tmp_vault):
    _write_rule(tmp_vault, "yarn-old", "Never use npm install; always yarn.", sources=[_src("old")])
    _write_rule(tmp_vault, "yarn-late", "Never npm install, npm install is banned, yarn yarn.",
                sources=[_src("late")])
    _write_rule(tmp_vault, "yarn-own", "npm install banned here, use yarn", sources=[_src("me")])
    _write_rule(tmp_vault, "docs-tone", "Write docs in plain words.", sources=[_src("old")])
    dates = tool.mrc.rule_dates(tmp_vault, learned={},
                                session_end={"old": 100.0, "late": 300.0, "me": 210.0})
    return tmp_vault, dates


def test_the_as_of_pool_drops_later_rules_and_the_corrections_own_session(rules_vault):
    _, dates = rules_vault
    assert tool.as_of_pool(dates, "me", 200.0) == {"yarn-old", "docs-tone"}
    # from another session's point of view, "me" is a source that had ended
    assert tool.as_of_pool(dates, "other", 250.0) == {"yarn-old", "docs-tone", "yarn-own"}


def test_ranking_goes_through_decide_and_sees_only_the_pool(rules_vault, monkeypatch):
    vault, dates = rules_vault
    idx = reflex_index.build_index(vault)
    seen = []
    from mnemo.core.reflex import decide as decide_mod
    real = decide_mod.decide

    def spy(index, **kw):
        seen.append(sorted(index["docs"]))
        return real(index, **kw)
    monkeypatch.setattr(decide_mod, "decide", spy)

    pool = tool.as_of_pool(dates, "me", 200.0)
    got = tool.ranking(idx, pool, "app", "npm install the deps with npm", {})
    assert got[0] == "yarn-old" and "yarn-late" not in got and "yarn-own" not in got
    assert seen == [["docs-tone", "yarn-old"]]
    # the reflex's own pre-gate: a two-word prompt gets no pool, as today
    assert tool.ranking(idx, pool, "app", "go on", {}) == []


def test_the_split_is_seeded_stable_and_about_half():
    ids = ["s-%d" % i for i in range(2000)]
    first = [tool.split_of(s) for s in ids]
    assert first == [tool.split_of(s) for s in ids]
    assert 0.45 < first.count(tool.TEST) / len(ids) < 0.55
    assert first != [tool.split_of(s, seed=1) for s in ids]


# --- positives and controls ------------------------------------------------------------------

def _labels(*pairs):
    """Columns as #519 files them, one per rater."""
    return {r: dict(pairs) for r in tool.mrc.RATERS}


def test_primary_targets_need_every_rater_to_name_a_vault_rule():
    items = [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]
    labels = _labels(("a", True), ("b", True), ("c", True), ("d", False))
    notes = {"R1": {"kind": "rule", "ref": "yarn-old"}, "R2": {"kind": "rule", "ref": "yarn-2"},
             "M1": {"kind": "memory", "ref": "MEMORY.md"}}
    units = {i: {"notes": notes} for i in "abcd"}
    r1, r2 = tool.mrc.RATERS
    verdicts = {
        r1: {"a": {"notes": ["R1"]}, "b": {"notes": ["M1"]}, "c": {"notes": ["R1"]}, "d": {"notes": ["R1"]}},
        r2: {"a": {"notes": ["R2", "M1"]}, "b": {"notes": ["R1"]}, "d": {"notes": ["R1"]}},
    }
    # a: both name a rule (the union is the target); b: one rater named only
    # memory; c: a rater has not answered; d: not a correction
    assert tool.primary_targets(items, labels, verdicts, units) == {"a": ["yarn-2", "yarn-old"]}


def test_hindsight_targets_are_what_every_rater_names_plus_the_primary_ones():
    rules = ["r-a", "r-b", "r-c"]
    assert tool.hindsight_targets({}, rules, [[0, 1], [1, 2]], []) == ["r-b"]
    assert tool.hindsight_targets({}, rules, [[0], [2]], ["old"]) == ["old"]
    assert tool.hindsight_targets({}, rules, [[0], None], []) is None
    assert tool.hindsight_targets({}, [], [], ["old"]) == ["old"]


def test_controls_are_edit_turns_whose_reply_is_not_a_correction():
    edit, read = [("Edit", "")], [("Read", "")]
    works = {"s1": [{"index": 0, "calls": edit}, {"index": 1, "calls": edit}, {"index": 2, "calls": read}],
             "s2": [{"index": 0, "calls": edit}]}
    got = tool.control_keys(works, {"s1": {1}}, limit=10)
    assert got == [("s1", 0), ("s2", 0)]
    assert len(tool.control_keys(works, {}, limit=1)) == 1
    assert tool.control_keys(works, {}, limit=1) == tool.control_keys(works, {}, limit=1)


def test_the_parsers_refuse_a_partial_answer():
    assert tool.parse_scores('{"scores": {"R1": 7, "R2": 12}}', ["a", "b"]) == {"a": 7.0, "b": 10.0}
    assert tool.parse_scores('{"scores": {"R1": 7}}', ["a", "b"]) is None
    assert tool.parse_broken('{"broken": {"R1": true, "R2": false}}', ["a", "b"]) == {"a": True, "b": False}
    assert tool.parse_broken('{"broken": {"R1": "yes"}}', ["a"]) is None
    assert tool.parse_ids('{"rules": ["R2", "r1", "R9"]}', "rules", 2) == [0, 1]
    assert tool.parse_ids("no json", "rules", 2) is None


# --- counting ----------------------------------------------------------------------------------

def _row(key, kind, split, rank, scores, targets=(), primary=(), rank_h=None):
    rank = {"prompt": list(rank[0]), "action": list(rank[1])}
    return {"key": key, "kind": kind, "split": split, "rank": rank,
            "rank_h": rank if rank_h is None else {"prompt": list(rank_h[0]), "action": list(rank_h[1])},
            "scores": scores, "targets": list(targets), "primary_targets": list(primary)}


def test_recall_counts_the_candidate_stage_and_the_judge_separately():
    rows = [_row("p:1", "positive", "test", (["x", "t"], ["t"]), {"t": 3, "x": 9}, ["t"]),
            _row("p:2", "positive", "test", (["x"], ["y", "t"]), {"t": 8}, ["t"])]
    assert tool.recall(rows, "prompt", 3, None) == [True, False]
    assert tool.recall(rows, "action", 3, None) == [True, True]
    assert tool.recall(rows, "action", 1, None) == [True, False]
    assert tool.recall(rows, "action", 3, 5) == [False, True]


def test_the_primary_reading_never_sees_the_hindsight_pool():
    # the later-extracted target "t" displaces the primary target in the
    # hindsight pool's top 1; the primary reading must not see that pool
    row = _row("p:1", "positive", "test", (["old"], ["old"]), {"t": 9, "old": 9}, targets=["t", "old"],
               primary=["old"], rank_h=(["t", "old"], ["t", "old"]))
    assert tool.recall([row], "action", 1, None, tool.PRIMARY) == [True]
    assert tool.recall([dict(row, primary_targets=["t"])], "action", 1, None, tool.PRIMARY) == [False]
    assert tool.recall([row], "action", 1, None, tool.HINDSIGHT) == [True]


def test_the_threshold_is_the_one_that_best_separates_positives_from_controls():
    pos = [_row("p:%d" % i, "positive", "dev", ([], ["t"]), {"t": s}, ["t"]) for i, s in enumerate((6, 7, 9))]
    ctl = [_row("c:%d" % i, "control", "dev", ([], ["n"]), {"n": s}) for i, s in enumerate((2, 4, 5, 6))]
    # t=6: recall 1 - 1/4 flagged; t=7: 2/3 - 0; t=5: 1 - 2/4
    assert tool.choose_threshold(pos, ctl, "action", 3) == 6
    # a tie goes to the higher threshold
    assert tool.choose_threshold([], [], "action", 3) == tool.THRESHOLDS[-1]


def test_paired_counts_and_an_honest_ci():
    assert tool.paired_counts([True, True, False, False], [True, False, True, False]) == (1, 1, 1, 1)
    d, lo, hi = tool.paired_ci(0, 1, 0, 0)          # one pair cannot decide
    assert d == 1.0 and lo < 0 < hi
    d, lo, hi = tool.paired_ci(5, 4, 4, 7)          # no difference
    assert d == 0 and lo < 0 < hi
    d2, lo2, hi2 = tool.paired_ci(5, 4, 4, 7)
    assert lo2 == pytest.approx(-hi)                # symmetric when b == c
    d, lo, hi = tool.paired_ci(10, 15, 0, 5)        # action alone hits 15 of 30
    assert d == pytest.approx(0.5) and lo > 0
    _, lo_swapped, _ = tool.paired_ci(10, 0, 15, 5)
    assert lo_swapped == pytest.approx(-hi)
    assert tool.paired_ci(0, 0, 0, 0) == (None, None, None)


def test_needed_pairs_is_the_first_n_whose_ci_excludes_zero():
    n = tool.needed_pairs(2, 4, 1, 3)
    assert n is not None
    shares = [x / 10 for x in (2, 4, 1, 3)]

    def lower(m):
        c = [int(round(s * m)) for s in shares]
        c[3] = m - sum(c[:3])
        return tool.paired_ci(*c)[1]
    assert lower(n) > 0 and lower(n - 1) <= 0
    assert tool.needed_pairs(2, 1, 1, 3) is None    # no effect, no size
    assert tool.needed_pairs(2, 0, 3, 3) is None


def test_a_flag_of_the_corrected_rule_is_right_unasked_and_others_need_every_rater():
    rated = {"m1": {"p:1": {"x": True}, "c:1": {"y": True}}, "m2": {"p:1": {"x": False}, "c:1": {"y": True}}}
    assert tool.flag_truth("p:1", "t", ["t"], rated) is True
    assert tool.flag_truth("p:1", "x", ["t"], rated) is False
    assert tool.flag_truth("c:1", "y", [], rated) is True
    assert tool.flag_truth("c:1", "z", [], rated) is None
    prec = tool.precision([("p:1", "t"), ("p:1", "x"), ("c:1", "z")],
                          {("p:1", "t"): True, ("p:1", "x"): False, ("c:1", "z"): None})
    assert (prec["k"], prec["n"], prec["pending"], prec["rate"]) == (1, 2, 1, 0.5)


def test_the_verdict_builds_only_when_both_conditions_hold_at_one_n():
    ok = {"diff": (0.3, 0.1, 0.5), "precision": {"rate": 0.9}, "n_primary": 40}
    weak = {"diff": (0.3, -0.1, 0.6), "precision": {"rate": 0.9}, "n_primary": 4}
    noisy = {"diff": (0.3, 0.1, 0.5), "precision": {"rate": 0.6}, "n_primary": 40}
    assert tool.verdict({3: weak, 10: ok}) == {"build": True, "n": 10, "reasons": []}
    v = tool.verdict({3: weak, 10: noisy})
    assert not v["build"] and len(v["reasons"]) == 2
    assert "CI lower" in v["reasons"][0] and "precision 60.0%" in v["reasons"][1]


def test_evaluate_chooses_on_dev_and_counts_on_test():
    rows = [
        # dev: action finds the target at 4, the control scores 3 -> threshold 4
        _row("p:d1", "positive", "dev", (["n"], ["t"]), {"t": 4, "n": 1}, ["t"], ["t"]),
        _row("c:d1", "control", "dev", (["n"], ["n"]), {"n": 3}),
        # test
        _row("p:t1", "positive", "test", (["n"], ["t"]), {"t": 9, "n": 1}, ["t"], ["t"]),
        _row("p:t2", "positive", "test", (["t"], ["t"]), {"t": 2}, ["t"], ["t"]),
        _row("c:t1", "control", "test", (["n"], ["n", "m"]), {"n": 7, "m": 1}),
        _row("c:t2", "control", "test", ([], []), {}),
    ]
    rated = {"m1": {"c:t1": {"n": False}}, "m2": {"c:t1": {"n": False}}}
    data = tool.evaluate(rows, rated)
    act = data["pools"][3]["arms"]["action"]
    assert act["threshold"] == 4
    assert act[tool.PRIMARY] == {"n": 2, "candidate": 2, "judged": 1}
    assert act["precision"]["k"] == 1 and act["precision"]["n"] == 2
    assert act["flags_per_100"] == 50.0 and act["false_per_100"] == 50.0
    assert data["pools"][3][tool.PRIMARY + "_paired"]["counts"] == (0, 1, 0, 1)
    assert data["counts"]["test_primary"] == 2 and data["counts"]["test_controls"] == 2
    assert data["kappa"]["n"] == 1
    assert not data["verdict"]["build"]
    lines = tool.report_lines(data, weekly=0.25)
    assert any("pilot" in line for line in lines)


# --- end to end -------------------------------------------------------------------------------

def _setup(tmp_path):
    projects, vault, source = tmp_path / "projects", tmp_path / "vault", tmp_path / "source"
    proj = projects / "-Users-you-github-app"
    proj.mkdir(parents=True)
    (vault / "shared").mkdir(parents=True)
    source.mkdir()
    events = [_user("install the deps", "2026-09-21T09:50:00Z"),
              _agent(_call("Bash", command="npm install"), _text("Installed with npm."),
                     ts="2026-09-21T09:55:00Z"),
              _result("2026-09-21T09:55:01Z"),
              _user("never use npm in this repo, always yarn", "2026-09-21T10:00:00Z"),
              _agent(_call("Edit", file_path="/r/b.py", old_string="a", new_string="b"),
                     _text("Switched."), ts="2026-09-21T10:01:00Z")]
    (proj / "sess0001-x.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    _write_rule(vault, "yarn-not-npm", "Never use npm install in this repo; always yarn.",
                sources=[_src("old00001-x")])
    _write_rule(vault, "yarn-learned-here", "Use yarn, never npm.", sources=[_src("sess0001-x")])
    (vault / ".mnemo").mkdir()
    (vault / ".mnemo" / "learned.jsonl").write_text(
        json.dumps({"slug": "yarn-not-npm", "ts": "2026-09-01T10:00:00Z"}) + "\n"
        + json.dumps({"slug": "yarn-learned-here", "ts": "2026-09-22T10:00:00Z"}) + "\n", encoding="utf-8")
    ts = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc).timestamp()
    item = {"id": "i1", "session_id": "sess0001-x", "project": "app", "turn_index": 1, "ts": ts,
            "quote": "never use npm in this repo", "rule": "always yarn", "turn": "never use npm",
            "answered": "Installed with npm."}
    cols = {tool.mrc.column(r, tool.mrc.LABEL_SYSTEM): {"i1": True} for r in tool.mrc.RATERS}
    (source / "items.json").write_text(json.dumps([item]), encoding="utf-8")
    (source / "labels.json").write_text(json.dumps(cols), encoding="utf-8")
    (source / "verdicts.json").write_text(json.dumps({tool.mrc.column(r, tool.mrc.JUDGE_SYSTEM): {
        "i1": {"known": True, "notes": ["R1"]}} for r in tool.mrc.RATERS}), encoding="utf-8")
    (source / "units.json").write_text(json.dumps(
        {"i1": {"notes": {"R1": {"kind": "rule", "ref": "yarn-not-npm"}}}}), encoding="utf-8")
    return projects, vault, source


def test_main_dry_sends_nothing_then_send_asks_each_step_once(tmp_path, capsys, monkeypatch):
    projects, vault, source = _setup(tmp_path)
    home = tmp_path / "claude-home"
    home.mkdir()
    base = ["--projects", str(projects), "--vault", str(vault), "--claude-home", str(home),
            "--source", str(source), "--out", str(tmp_path / "out"), "--rater", "m1", "--rater", "m2",
            "--pause", "0"]
    monkeypatch.setattr(llm, "resolve", lambda cfg: pytest.fail("a dry run must not resolve a provider"))
    assert tool.main(base + ["--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["counts"]["positives"] == 1 and data["counts"]["primary"] == 1
    # one positive (the npm install) and one control (the edit after the correction)
    assert data["counts"]["controls"] == 1
    assert "targets, m1: 1 call(s)" in captured.err and "judge claude-haiku-5-5: 2 turn(s)" in captured.err

    asked = []

    def provider(prompt, *, system, model, timeout):
        asked.append((model, system))
        if system == tool.TARGET_SYSTEM:
            body = {"rules": ["R1"]}
        elif system == tool.JUDGE_SYSTEM:
            n = prompt.count("\n### R")
            body = {"scores": {"R%d" % i: 9 for i in range(1, n + 1)}}
        else:
            body = {"broken": {}}
        return llm.LLMResponse(text=json.dumps(body), total_cost_usd=0.0, input_tokens=1,
                               output_tokens=1, api_key_source=None, raw={})
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    assert tool.main(base + ["--send", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["counts"]["hindsight_with_target"] == 1 and data["counts"]["judged"] >= 1
    systems = [s for _, s in asked]
    assert systems.count(tool.TARGET_SYSTEM) == 2 and systems.count(tool.JUDGE_SYSTEM) >= 1
    # the turn the correction answered is the npm install, opened by the first prompt
    scores = json.loads((tmp_path / "out" / "scores.json").read_text(encoding="utf-8"))
    assert any(k.startswith("p:i1") for col in scores.values() for k in col)

    asked.clear()
    assert tool.main(base + ["--send", "--json"]) == 0
    assert [s for _, s in asked if s != tool.RATE_SYSTEM] == []


def test_the_curve_counts_both_splits_at_every_threshold():
    pos = [_row("p:1", "positive", "dev", ([], ["t"]), {"t": 6}, ["t"]),
           _row("p:2", "positive", "test", (["t"], []), {"t": 9}, ["t"])]
    ctl = [_row("c:1", "control", "test", ([], ["n"]), {"n": 4}),
           _row("c:2", "control", "dev", ([], ["n"]), {"n": 1})]
    c = tool.curve(pos, ctl)
    act = c["3/action"]
    assert act["candidate"] == 1
    at = {p["t"]: p for p in act["points"]}
    assert (at[4]["recalled"], at[4]["flags_per_100"]) == (1, 50.0)
    assert (at[5]["recalled"], at[5]["flags_per_100"]) == (1, 0.0)
    assert at[7]["recalled"] == 0
    assert {p["t"]: p["recalled"] for p in c["3/prompt"]["points"]}[9] == 1
