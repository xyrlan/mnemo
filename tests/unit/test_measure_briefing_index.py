"""``tools/measure_briefing_index.py`` over synthetic transcripts, briefings and raters (#548)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_briefing_index as tool  # noqa: E402
from tools import measure_briefing_value as value  # noqa: E402

from mnemo.core import llm  # noqa: E402
from mnemo.hooks import session_start as ss  # noqa: E402

from tests.unit.test_measure_briefing_value import (  # noqa: E402
    HEAD, OLD, _cand, _labels, _resp, _session, _setup, _unit)

R1, R2 = tool.RATERS
EXPORT = "# Briefing\n\n## TL;DR\n\nThe csv exporter drops the header row; fix next.\n\n## What I did\n\n- looked"


# --- the index -----------------------------------------------------------------------------

def test_tldr_is_the_section_else_the_first_paragraph():
    assert tool.tldr(EXPORT) == "The csv exporter drops the header row; fix next."
    assert tool.tldr("# Briefing\n\n## TL;DR\nOne.\nTwo.\n") == "One.\nTwo."
    assert tool.tldr("# Briefing\n\nNo sections here.\n\nMore.") == "No sections here."
    assert tool.tldr("") == ""


def test_pool_is_the_ten_newest_newest_first():
    u = {"candidates": [_cand("c%d" % r, r, "b") for r in (3, 0, 12, 9, 1)]}
    assert [c["id"] for c in tool.pool_of(u)] == ["c0", "c1", "c3", "c9"]


def test_fair_cut_gives_short_texts_whole_and_splits_the_rest():
    assert tool.fair_cut([10, 20, 30], 100) == [10, 20, 30]
    assert tool.fair_cut([10, 100, 100], 110) == [10, 50, 50]
    assert tool.fair_cut([100, 100, 100], 90) == [30, 30, 30]
    assert tool.fair_cut([10, 10], -5) == [0, 0]
    assert sum(tool.fair_cut([7, 300, 45, 900], 400)) <= 400


def test_cut_to_falls_on_a_word_and_is_marked():
    assert tool.cut_to("short", 10) == "short"
    got = tool.cut_to("alpha beta gamma delta", 15)
    assert got == "alpha beta" + tool.ELLIPSIS and len(got.encode("utf-8")) <= 15
    assert tool.cut_to("ação " * 20, 11).endswith(tool.ELLIPSIS)
    assert tool.cut_to("anything", 2) == ""


def test_index_block_lists_date_and_tldr_newest_first_under_the_room():
    entries = [({"date": "2026-09-21"}, "Newest."), ({"date": "2026-09-20"}, "Older."), ({}, "Undated.")]
    block, cut = tool.index_block(entries, 10_000)
    assert cut == 0 and block.startswith("\n\n[recent-briefings count=3")
    assert block.endswith("[/recent-briefings]")
    assert block.index("### 2026-09-21\nNewest.") < block.index("### 2026-09-20\nOlder.") < block.index(
        "### undated\nUndated.")
    long = [({"date": "2026-09-2%d" % i}, " ".join(["word%d" % i] * 200)) for i in range(4)] + [
        ({"date": "2026-09-19"}, "Tiny.")]
    block, cut = tool.index_block(long, 2000)
    assert cut == 4 and len(block.encode("utf-8")) <= 2000 and "### 2026-09-19\nTiny." in block
    assert tool.index_block([], 100) == ("", 0)


def _metas():
    return {"b-old": {"meta": {"session_id": "b-old", "date": "2026-09-19"}},
            "b-export": {"meta": {"session_id": "b-export", "date": "2026-09-20"}}}


def _index_unit(uid="s1", hook_first="b-old"):
    u = _unit(uid, hook_first)
    u["candidates"][1]["body"] = EXPORT
    u["candidates"].append(_cand("b-ancient", 14, "# Briefing\n\n## TL;DR\n\nFar outside the pool."))
    return u


def test_index_arm_differs_from_none_only_in_the_slot_and_fits_the_cap():
    arm = tool.build_index_arm(_index_unit(), value.first_context(_session()), _metas())
    frozen = value.build_arms(_unit(), value.first_context(_session()), "b-export", "agreed best", "b-old", {})
    assert arm["none"] == frozen["prompts"][value.NONE]
    p = arm["prompt"]
    assert "[last-briefing" not in p and HEAD in p and "Use yarn." in p and "[[app__x]]" in p
    assert "The csv exporter drops the header row; fix next." in p and "What I did" not in p
    assert "We shipped the login page." in p and "Unrelated." in p and "Far outside" not in p
    assert p.index("### 2026-09-19") < p.index("### 2026-09-20")  # rank 0 first
    assert arm["ids"] == ["b-old", "b-export", "b-far"] and arm["entries"] == 3 and arm["cut"] == 0
    assert arm["envelope_bytes"] <= ss.ENVELOPE_MAX_BYTES
    assert arm["index_bytes"] == len(p[p.index("[recent-briefings"):p.index("[/recent-briefings]")].encode()) + len(
        "[/recent-briefings]")


def test_a_long_pool_is_cut_fairly_to_the_cap():
    u = _index_unit()
    u["candidates"] = [_cand("b%d" % r, r, "## TL;DR\n\n" + " ".join(["w%d" % r] * 900)) for r in range(10)]
    arm = tool.build_index_arm(u, value.first_context(_session()), {})
    assert arm["cut"] == 10 and arm["envelope_bytes"] <= ss.ENVELOPE_MAX_BYTES
    assert arm["envelope_bytes"] > ss.ENVELOPE_MAX_BYTES - 200


# --- the judge ---------------------------------------------------------------------------

def _value_arms():
    return value.build_arms(_unit(), value.first_context(_session()), "b-export", "agreed best", "b-old", {})


def test_the_rater_never_sees_a_briefing_and_the_order_flips():
    arm = tool.build_index_arm(_index_unit(), value.first_context(_session()), _metas())
    mine = [{"text": "Per [recent-briefings count=3] and b-far: fix the header."}]
    theirs = {value.NONE: [{"text": "What export bug?"}],
              value.RIGHT: [{"text": "[last-briefing session=b-export] says fix the header."}]}
    for control in tool.CONTROLS:
        p0 = tool.comparison_prompt(_value_arms(), arm, mine, theirs, control, 0, 0)
        p1 = tool.comparison_prompt(_value_arms(), arm, mine, theirs, control, 0, 1)
        for p in (p0, p1):
            assert "b-far" not in p and "b-export" not in p and "[recent-briefings" not in p
            assert "[last-briefing" not in p and "csv exporter drops" not in p and "PR #12 open" in p
        first = lambda p: p.split("## Reply 1")[1].split("## Reply 2")[0]  # noqa: E731
        assert ("Per [...]" in first(p0)) != ("Per [...]" in first(p1))
        assert tool.comparison_prompt(_value_arms(), arm, mine, theirs, control, 1, 0) is None
    assert tool.comparison_prompt(_value_arms(), arm, [], theirs, tool.NONE, 0, 0) is None


def test_parse_judge_names_the_control():
    for control in tool.CONTROLS:
        cid = tool.comparison_id("s1", control, 0)
        for ri in (0, 1):
            one = value.treatment_first(cid, ri)
            got = tool.parse_judge(json.dumps({"better": "1", "wrong_state": {"1": True, "2": False}}),
                                   control, cid, ri)
            assert got["better"] == (tool.INDEX if one else control)
            assert got["wrong"] == ({tool.INDEX: True, control: False} if one else {tool.INDEX: False, control: True})
        assert tool.parse_judge("nope", control, cid, 0) is None


# --- the numbers -------------------------------------------------------------------------

def _v(better, wa=False, wc=False, control=tool.NONE):
    return {"better": better, "wrong": {tool.INDEX: wa, control: wc}}


def test_unit_scores_kappa_and_the_share_kept():
    cid = tool.comparison_id
    v = {R1: {cid("a", "none", 0): _v("index"), cid("a", "none", 1): _v("none", wa=True),
              cid("b", "none", 0): _v("index"), cid("b", "none", 1): _v("tie")},
         R2: {cid("a", "none", 0): _v("index"), cid("a", "none", 1): _v("tie", wa=True),
              cid("b", "none", 0): _v("index"), cid("b", "none", 1): _v("tie")}}
    a = tool.unit_scores(v, "a", tool.NONE, tool.RATERS)
    assert (a["h"], a["arm"], a["tie"], a["wrong_arm"], a["wrong_diff"]) == (0.5, 0.5, 0.5, 0.5, 0.5)
    assert tool.unit_scores(v, "c", tool.NONE, tool.RATERS) is None
    st = tool.pair_stats(v, ["a", "b", "c"], tool.NONE, tool.RATERS)
    assert st["n"] == 2 and st["h"] == 0.5
    k = tool.kappa(v, [cid(u, "none", i) for u in "ab" for i in (0, 1)], tool.RATERS)
    assert (k["n"], k["same"], k["wrong_n"]) == (4, 3, 8)

    vc = value.comparison_id
    rv = {r: {vc(u, "right", i): {"better": "right", "wrong": {"right": False, "none": False}}
              for u in "ab" for i in (0, 1)} for r in tool.RATERS}
    got = tool.kept(v, rv, ["a", "b"], tool.RATERS)
    assert got["n"] == 2 and got["h_right"] == 1.0 and got["h_index"] == 0.5 and got["share"] == 0.5
    assert got["share_ci"][0] <= 0.5 <= got["share_ci"][1]


def test_verdict_follows_the_preset_bar():
    def st(h, lo, hi):
        return {"n": 100, "h": h, "h_ci": [lo, hi]}
    assert tool.verdict(st(0.2, 0.05, 0.3), 0.5) == {"ruler": "ok", "index": "the index helps"}
    assert tool.verdict(st(0.2, -0.01, 0.3), 0.5)["index"] == "inconclusive"
    assert tool.verdict(st(0.14, 0.01, 0.3), 0.5)["index"] == "inconclusive"
    assert tool.verdict(st(0.0, -0.1, 0.12), 0.5)["index"] == "the index does not measurably help"
    assert tool.verdict(st(0.3, 0.2, 0.4), 0.3)["ruler"] == "too weak"
    assert tool.verdict({"n": 0}, None)["index"] == "no estimate yet"


# --- end to end --------------------------------------------------------------------------

def _judge(prompt):
    replies = prompt.split("## Reply 1")[1].split("## What the session")[0].split("## Reply 2")
    score = [("header" in r) - ("login" in r) for r in replies]
    better = "1" if score[0] > score[1] else "2" if score[1] > score[0] else "tie"
    return _resp(json.dumps({"better": better, "why": "w",
                             "wrong_state": {"1": "login" in replies[0], "2": "login" in replies[1]}}))


def test_run_reuses_540s_cache_answers_judges_and_a_rerun_asks_nothing(tmp_path, capsys, monkeypatch):
    base = _setup(tmp_path)
    monkeypatch.chdir(tmp_path)

    def provider_540(prompt, *, system, model, timeout):
        if system == value.rl.ARM_SYSTEM:
            if "fix next" in prompt:
                return _resp("Fixing the csv header row next.")
            if "login page" in prompt:
                return _resp("The login page is done, so I will polish it.")
            return _resp("Which export bug do you mean?")
        return _judge(prompt)
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider_540)
    assert value.main(base + ["--send", "--json"]) == 0
    capsys.readouterr()
    value_dir = tmp_path / "out"
    before = {p.name: p.read_bytes() for p in value_dir.iterdir()}

    args = [a for a in base if a != str(value_dir)]
    args[args.index("--out")] = "--value"
    args[args.index("--value") + 1:args.index("--value") + 1] = [str(value_dir)]
    args += ["--out", str(tmp_path / "index")]
    monkeypatch.setattr(llm, "resolve", lambda cfg: pytest.fail("a dry run must not resolve a provider"))
    assert tool.main(args + ["--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["counts"]["built"] == 2 and data["counts"]["fair"] == 2 and data["counts"]["right_in_index"] == 2
    assert data["pending"]["answers"] == 2 * 2

    asked = []

    def provider(prompt, *, system, model, timeout):
        asked.append((model, system))
        if system == tool.rl.ARM_SYSTEM:
            assert "[recent-briefings count=3" in prompt and "[last-briefing" not in prompt
            return _resp("The index says the csv header row is next; fixing it.")
        assert system == tool.JUDGE_SYSTEM and "[recent-briefings" not in prompt and "fix next" not in prompt
        return _judge(prompt)
    monkeypatch.setattr(llm, "resolve", lambda cfg: provider)
    assert tool.main(args + ["--send", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    systems = [s for _, s in asked]
    assert systems.count(tool.rl.ARM_SYSTEM) == 4 and systems.count(tool.JUDGE_SYSTEM) == 2 * 2 * 2 * 2
    assert {m for m, s in asked if s == tool.rl.ARM_SYSTEM} == {"claude-opus-5-5", "claude-fable-5-1"}
    both = data["results"]["both"]
    assert both["none"]["n"] == 2 and both["none"]["h"] == 1.0
    assert both["right"]["n"] == 2 and both["right"]["h"] == 0.0 and both["right"]["tie"] == 1.0
    assert data["kept"]["share"] == 1.0 and data["verdict"]["index"] == "the index helps"
    assert data["size"]["index_bytes"]["n"] == 2
    # #540's cache is read, never written
    assert {p.name: p.read_bytes() for p in value_dir.iterdir()} == before

    asked.clear()
    assert tool.main(args + ["--send"]) == 0
    out = capsys.readouterr().out
    assert asked == [] and "index: the index helps" in out and "h_index" in out and "index size" in out
