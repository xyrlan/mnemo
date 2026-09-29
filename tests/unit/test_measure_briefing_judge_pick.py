"""``tools/measure_briefing_judge_pick.py`` over synthetic sessions, briefings and a stub model (#547)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_briefing_judge_pick as tool  # noqa: E402

from mnemo.core.llm import LLMResponse  # noqa: E402

mbp, mrc = tool.mbp, tool.mrc
HAIKU, SONNET = tool.MODELS


def _body(tldr, state="short state", more="## What I did\n\nSECRET DETAIL\n"):
    return "# Briefing — app — x\n\n## TL;DR\n\n%s\n\n%s\n## State at end of session\n\n%s\n" % (tldr, more, state)


def _cand(cid, rank, body):
    return {"id": cid, "rank": rank, "signals": {}, "hook_first": rank == 0, "body": body}


def _unit(uid, prompt, cands, start=1790000000.0):
    return {"id": uid, "start": start, "first_prompt": prompt, "candidates": cands}


# --- the picker's view ---------------------------------------------------------------------

def test_excerpt_keeps_the_tldr_and_a_short_state_only():
    text = tool.excerpt(_body("Did the thing.", state="Branch x, PR #9 open."))
    assert text.startswith("TL;DR: Did the thing.")
    assert "State at end of session:\nBranch x, PR #9 open." in text
    assert "SECRET DETAIL" not in text
    long_state = tool.excerpt(_body("Did it.", state="s" * (tool.STATE_CHARS + 1)))
    assert "State at end" not in long_state
    capped = tool.excerpt(_body("t" * 5000))
    assert len(capped) <= tool.NOTE_CHARS + 1 and "t" * tool.TLDR_CHARS in capped
    assert tool.excerpt("# Title\n\nno sections here") == "TL;DR: no sections here"


def test_build_inputs_takes_the_ten_newest_with_their_dates(tmp_path):
    d = tmp_path / "bots" / "app" / "briefings" / "sessions"
    d.mkdir(parents=True)
    (d / "c3.md").write_text("---\nsession_id: c3\ndate: 2026-09-01\n---\n\nbody\n", encoding="utf-8")
    dates = tool.briefing_dates(tmp_path)
    assert dates == {"c3": "2026-09-01"}
    cands = [_cand("c%d" % r, r, _body("n%d" % r)) for r in (11, 3, 0, 9, 10)]
    [u] = tool.build_inputs([_unit("u1", "go", cands)], dates)
    assert [n["id"] for n in u["notes"]] == ["c0", "c3", "c9"]
    assert [n["date"] for n in u["notes"]] == ["", "2026-09-01", ""]
    assert u["part"] == mbp.part_of("u1") and u["first_prompt"] == "go" and u["date"]


def test_letters_are_a_seeded_shuffle_and_the_prompt_shows_only_the_excerpt():
    notes = [{"id": "c%d" % i, "rank": i, "date": "2026-09-%02d" % (i + 1), "text": "TL;DR: note %d" % i}
             for i in range(10)]
    unit = {"id": "u1", "part": "dev", "date": "2026-09-20", "first_prompt": "fix the parser", "notes": notes}
    order = tool.letters_for(unit)
    assert order == tool.letters_for(unit) and sorted(order) == sorted(n["id"] for n in notes)
    assert order != [n["id"] for n in notes] or tool.letters_for(dict(unit, id="u2")) != order
    prompt = tool.picker_prompt(unit)
    assert "fix the parser" in prompt and "today is 2026-09-20" in prompt
    first = prompt.index("### Note A")
    assert prompt[first:].startswith("### Note A (written 2026-09-%02d)" % (int(order[0][1:]) + 1))
    assert len(re.findall(r"^### Note [A-J]", prompt, re.M)) == 10


def test_parse_pick_reads_the_first_line():
    order = ["x", "y", "z"]
    assert tool.parse_pick("none\n\nNo note is about it.", order) == (None, True)
    assert tool.parse_pick("C", order) == ("z", True)
    assert tool.parse_pick("**B**\nbecause…", order) == ("y", True)
    assert tool.parse_pick("Note A", order) == ("x", True)
    assert tool.parse_pick("b — it names #12", order) == ("y", True)
    assert tool.parse_pick('{"pick": "A"}', order) == ("x", True)
    assert tool.parse_pick("D", order) == (None, False)  # no such note
    assert tool.parse_pick("A note about the parser", order) == (None, False)
    assert tool.parse_pick("", order) == (None, False)


# --- scoring -------------------------------------------------------------------------------

def _answer(unit, pick_id, **kw):
    order = tool.letters_for(unit)
    reply = "none" if pick_id is None else tool._LETTERS[order.index(pick_id)]
    return dict({"reply": reply, "wall_s": 1.0, "api_ms": 500, "usd": 0.001, "workers": 1}, **kw)


def _units(n):
    notes = [{"id": "n%d" % i, "rank": i, "date": "", "text": "t"} for i in range(3)]
    return [{"id": "u%d" % i, "part": "dev", "date": "", "first_prompt": "p", "notes": notes} for i in range(n)]


def test_score_column_rereads_replies_and_counts_what_it_could_not():
    units = _units(4)
    truth = {"u0": {"n0"}, "u1": {"n1"}, "u2": set(), "u3": {"n2"}}
    col = {"u0": _answer(units[0], "n0"), "u1": _answer(units[1], "n2"), "u2": _answer(units[2], None),
           "u3": dict(_answer(units[3], None), reply="I think maybe")}
    s = tool.score_column(units, truth, col)
    assert (s["injected"], s["right"], s["with_right"], s["answered"]) == (2, 1, 3, 4)
    assert s["precision"] == 0.5 and abs(s["coverage"] - 1 / 3) < 1e-9
    assert s["unparsed"] == 1 and s["said_none_with_right"] == 1
    # an unanswered unit is left out, not counted as a none
    assert tool.score_column(units, truth, {"u0": col["u0"]})["sessions"] == 1


def test_choose_takes_coverage_at_the_bar_else_the_most_precise():
    def s(p, c, inj):
        return {"precision": p, "coverage": c, "injected": inj}

    assert tool.choose({"base": s(0.9, 0.5, 10), "strict": s(0.95, 0.3, 5), "lenient": s(0.7, 0.9, 30)}) \
        == ("base", True)
    assert tool.choose({"base": s(0.8, 0.5, 12), "strict": s(0.85, 0.5, 9)}) == ("strict", True)
    assert tool.choose({"base": s(0.6, 0.5, 10), "strict": s(0.7, 0.2, 4), "lenient": s(None, 0.0, 0)}) \
        == ("strict", False)


def test_timing_and_api_ms():
    rows = [{"wall_s": w, "api_ms": 1000 * w, "usd": 0.002, "workers": 1} for w in (1, 2, 3, 4, 10)]
    t = tool.timing(rows)
    assert t["wall_median_s"] == 3 and t["wall_p90_s"] == 10 and t["api_median_s"] == 3.0
    assert abs(t["usd_per_session"] - 0.002) < 1e-12 and t["workers"] == [1]
    assert tool.timing([])["wall_median_s"] is None
    assert tool.api_ms({"events": [{"type": "x"}, {"duration_api_ms": 812}]}) == 812.0
    assert tool.api_ms({"duration_api_ms": 5}) == 5.0 and tool.api_ms(None) is None


def test_timed_records_wall_time_per_call():
    ticks = iter([10.0, 12.5])
    call, seen = tool.timed(lambda p, **kw: "resp", clock=lambda: next(ticks))
    assert call("hi", system="s", model="m", timeout=1) == "resp"
    assert seen == {("m", "s", "hi"): 2.5}


# --- end to end ----------------------------------------------------------------------------

class Stub:
    """Names the note whose excerpt holds the first prompt's word; 'none' for 'x'.
    The strict wording is made to say none always, so the lock has a choice."""

    def __init__(self):
        self.calls = []

    def __call__(self, prompt, *, system, model, timeout):
        self.calls.append((model, system, prompt))
        word = prompt.split("## The developer's first message", 1)[1].splitlines()[1].strip()
        text = "none"
        if "clearly continues it" not in system:
            for m in re.finditer(r"^### Note ([A-J])[^\n]*\n(.*?)(?=^### Note |\Z)", prompt, re.M | re.S):
                if word in m.group(2):
                    text = m.group(1) + "\n\nbecause it names " + word
        return LLMResponse(text=text, total_cost_usd=0.001, input_tokens=100, output_tokens=2,
                           api_key_source=None, raw={"events": [{"duration_api_ms": 300}]})


def _world(tmp_path, n=16):
    vault = tmp_path / "vault"
    source = vault / ".mnemo" / tool.SOURCE_DIR
    units, about = [], {}
    for i in range(n):
        uid = "sess-%02d" % i
        word = "topic%02d" % i
        cands = [_cand("%s-c%d" % (uid, r), r, _body("unrelated work %d" % r)) for r in range(4)]
        if i % 4 != 3:  # three in four sessions have a right note
            cands[2] = _cand("%s-c2" % uid, 2, _body("worked on %s" % word))
            about[uid] = ["%s-c2" % uid]
        units.append(_unit(uid, word if i % 4 != 3 else "x", cands))
    source.mkdir(parents=True)
    (source / "units.json").write_text(json.dumps({"units": units}), encoding="utf-8")
    labels = {mrc.column(r, mbp.SYSTEM): {u["id"]: {"about": about.get(u["id"], []), "best": None}
                                          for u in units} for r in mbp.RATERS}
    (source / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    return vault


def test_main_sends_dev_locks_scores_test_once_and_resumes(tmp_path, capsys):
    vault = _world(tmp_path)
    out = vault / ".mnemo" / tool.OUT_DIR
    stub = Stub()
    argv = ["--vault", str(vault), "--pause", "0", "--workers", "2"]
    assert tool.main(argv, provider=stub) == 0
    assert stub.calls == []  # nothing leaves without --send
    assert tool.main(argv + ["--send"], provider=stub) == 0
    inputs = json.loads((out / "inputs.json").read_text(encoding="utf-8"))["units"]
    n_dev = sum(1 for u in inputs if u["part"] == "dev")
    assert n_dev and len(stub.calls) == n_dev * len(tool.MODELS) * len(tool.VARIANTS)
    assert all("SECRET DETAIL" not in p for _, _, p in stub.calls)
    assert all(p.index("## Notes") > p.index(m) for _, _, p in stub.calls for m in ("first message",))
    sent = len(stub.calls)
    assert tool.main(argv + ["--send"], provider=stub) == 0
    assert len(stub.calls) == sent  # cached: a rerun resumes
    assert tool.main(argv + ["--score-test"], provider=stub) == 2  # no lock yet
    assert tool.main(argv + ["--lock"], provider=stub) == 0
    lock = json.loads((out / "lock.json").read_text(encoding="utf-8"))
    assert {m: v["variant"] for m, v in lock["models"].items()} == {HAIKU: "base", SONNET: "base"}
    assert all(v["met_bar_on_dev"] for v in lock["models"].values())
    assert tool.main(argv + ["--score-test"], provider=stub) == 0
    n_test = len(inputs) - n_dev
    test_calls = stub.calls[sent:]
    assert len(test_calls) == n_test * len(tool.MODELS)
    assert {s for _, s, _ in test_calls} == {tool.VARIANTS["base"]}
    test = json.loads((out / "test.json").read_text(encoding="utf-8"))
    for model in tool.MODELS:
        r = test["models"][model]
        assert r["passed"] and r["score"]["precision"] == 1.0 and r["score"]["coverage"] == 1.0
    assert test["timing"][HAIKU]["usd_per_session"] == pytest.approx(0.001)
    assert "newest" in test["baselines"]
    before = (out / "test.json").read_text(encoding="utf-8")
    assert tool.main(argv + ["--score-test"], provider=stub) == 0
    assert (out / "test.json").read_text(encoding="utf-8") == before  # scored once
    assert len(stub.calls) == sent + len(test_calls)
    printed = capsys.readouterr().out
    assert "TEST half, scored once" in printed and "PASS" in printed and "latency wall median" in printed


def test_score_test_waits_for_every_answer(tmp_path):
    vault = _world(tmp_path)
    out = vault / ".mnemo" / tool.OUT_DIR
    stub = Stub()
    argv = ["--vault", str(vault), "--pause", "0"]
    tool.main(argv + ["--send"], provider=stub)
    tool.main(argv + ["--lock"], provider=stub)
    tool.main(argv + ["--score-test", "--limit", "1"], provider=stub)
    assert not (out / "test.json").exists()
    tool.main(argv + ["--score-test"], provider=stub)
    assert (out / "test.json").exists()


def test_a_missing_source_is_an_error(tmp_path):
    assert tool.main(["--vault", str(tmp_path)], provider=Stub()) == 2
