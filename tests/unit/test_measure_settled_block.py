"""``tools/measure_settled_block.py`` over hand-built caches whose answers are
known by construction (#598)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_settled_block as tool  # noqa: E402

DOCS = {
    "a": {"projects": ["app"]},
    "b": {"projects": ["app"]},
    "c": {"universal": True},
    "d": {"projects": ["other"]},
    "e": {"projects": ["app"], "retired": True},
}
#: Every rule learned at t=10 (a learned.jsonl row), except "late" at t=100.
DATES = {s: {"stamped": 10.0, "has_row": True, "sources": []} for s in DOCS}
DATES["late"] = {"stamped": 100.0, "has_row": True, "sources": []}
DOCS["late"] = {"projects": ["app"]}


def _block(events, sid="s", project="app", start=50.0, k=15, exclude=()):
    return tool.block_at(sid, project, start, events, DOCS, DATES, k, exclude)


# --- 2. the block, as of the session's start ---------------------------------------------

def test_block_ranks_by_distinct_sessions_ties_by_slug():
    events = {
        "a": [("x", None)],
        "b": [("x", None), ("y", 20.0), ("y", 21.0), ("z", 30.0)],  # y counted once
        "c": [("x", None), ("w", 20.0)],
    }
    assert _block(events) == ["b", "c", "a"]
    assert _block(events, k=2) == ["b", "c"]


def test_block_counts_only_corrections_before_the_start():
    events = {"a": [("x", None), ("y", 60.0), ("z", 70.0)], "b": [("x", None), ("w", 40.0)]}
    assert _block(events, start=50.0) == ["b", "a"]
    assert _block(events, start=80.0) == ["a", "b"]


def test_block_holds_only_rules_that_existed_then():
    events = {"late": [("x", None), ("y", 1.0), ("z", 2.0)], "a": [("x", None)]}
    assert _block(events, start=50.0) == ["a"]
    assert _block(events, start=150.0) == ["late", "a"]


def test_block_never_counts_the_session_itself():
    events = {"a": [("s", None)], "b": [("s", 20.0), ("x", 20.0)]}
    assert _block(events, sid="s") == ["b"]


def test_block_needs_a_correction():
    assert _block({"a": []}) == []


def test_block_respects_project_retired_and_exclusion():
    events = {s: [("x", None)] for s in ("a", "b", "c", "d", "e")}
    assert _block(events) == ["a", "b", "c"]
    assert _block(events, exclude={"b"}) == ["a", "c"]
    assert _block(events, project="other") == ["c", "d"]


def test_correction_events_sources():
    evidence = {"a": {"session_id": "x"}, "b": {"session_id": "y"}}
    got = tool.correction_events({"a"}, evidence, known=[("c", "z", 5.0)], linked=[("a", "w", 6.0)])
    assert got == {"a": [("x", None), ("w", 6.0)], "c": [("z", 5.0)]}


# --- 3. coverage and the estimate ----------------------------------------------------------

UNITS = [
    {"session_id": "s1", "slug": "a", "strict": True, "redundant": False},
    {"session_id": "s1", "slug": "b", "strict": True, "redundant": True},
    {"session_id": "s1", "slug": "c", "strict": False, "redundant": False},
    {"session_id": "s2", "slug": "a", "strict": True, "redundant": False},
    {"session_id": "s2", "slug": "d", "strict": False, "redundant": False},
]


def test_session_rows_coverage_by_block():
    blocks = {"s1": ["a", "b"], "s2": []}
    strict = tool.session_rows(UNITS, ["s1", "s2", "s3"], blocks, tool.STRICT)
    assert [(r["units"], r["delivered"], r["new"], r["fresh"]) for r in strict] == [(2, 2, 1, 1), (1, 0, 0, 1), (0, 0, 0, 0)]
    broad = tool.session_rows(UNITS, ["s1", "s2"], blocks, tool.BROAD)
    assert [(r["units"], r["delivered"], r["new"]) for r in broad] == [(3, 2, 1), (2, 0, 0)]


def test_session_rows_ceiling_delivers_every_non_redundant_unit():
    rows = tool.session_rows(UNITS, ["s1", "s2"], {}, tool.BROAD, ceiling=True)
    assert [(r["delivered"], r["new"]) for r in rows] == [(3, 2), (2, 2)]


def test_ratio_ci_point_and_bounds():
    rows = [{"k": 1, "n": 2}, {"k": 0, "n": 2}, {"k": 2, "n": 2}]
    got = tool.ratio_ci(rows, "k", "n", n_boot=500)
    assert (got["k"], got["n"], got["share"]) == (3, 6, 0.5)
    assert 0.0 <= got["ci"][0] < 0.5 < got["ci"][1] <= 1.0
    same = [{"k": 1, "n": 2}] * 5
    assert tool.ratio_ci(same, "k", "n", n_boot=200)["ci"] == [0.5, 0.5]
    assert tool.ratio_ci([{"k": 0, "n": 0}], "k", "n")["share"] is None


def _rows(news, n):
    return [{"units": 1, "new": int(i < news), "delivered": int(i < news), "redundant": 0, "prompts": 0,
             "session_start": 0, "reflex": 0, "mcp": 0} for i in range(n)]


def test_estimate_is_new_per_session_times_lift():
    got = tool.estimate(_rows(30, 100), [0.5] * 20)
    assert abs(got["E"] - 0.15) < 1e-9
    assert got["verdict"] == "positive"
    assert tool.estimate(_rows(0, 3000), [0.5] * 20)["verdict"] == "null"


def test_decide_follows_the_declared_bar():
    pos = {"E": 0.2, "ci": {"E": [0.1, 0.3]}, "verdict": "positive"}
    low = {"E": 0.01, "ci": {"E": [0.0, 0.02]}, "verdict": "null"}
    wide = {"E": 0.07, "ci": {"E": [0.0, 0.2]}, "verdict": "inconclusive"}
    assert tool.decide({}, {tool.STRICT: pos, tool.BROAD: low}) == "build"
    assert tool.decide({}, {tool.STRICT: low, tool.BROAD: pos}) == "build"
    assert tool.decide({tool.STRICT: low, tool.BROAD: low}, {tool.STRICT: low, tool.BROAD: low}) == "do not build"
    assert tool.decide({tool.STRICT: low, tool.BROAD: pos}, {tool.STRICT: low, tool.BROAD: low}) == "inconclusive"
    assert tool.decide({tool.STRICT: low, tool.BROAD: pos}, {tool.STRICT: low, tool.BROAD: wide}) == "inconclusive"


# --- 4. cost ----------------------------------------------------------------------------------

def test_block_bytes_renders_whole_bodies():
    assert tool.block_bytes([], {}) == 0
    got = tool.block_bytes(["a", "b"], {"a": "body é", "b": "x"})
    text = tool.HEADER + "\n• [[a]]:\nbody é\n• [[b]]:\nx"
    assert got == len(text.encode("utf-8"))


def test_quantile():
    assert tool.quantile([], 0.5) is None
    assert tool.quantile([5, 1, 3], 0.5) == 3
    assert tool.quantile(list(range(101)), 0.95) == 95


# --- reading #519's and the ledger's corrections -----------------------------------------------

def _rc_dir(tmp_path):
    mrc = tool.mrc
    d = tmp_path / "rc"
    d.mkdir()
    items = [{"id": "i1", "session_id": "s1", "ts": 5.0, "quote": "Stop doing X"},
             {"id": "i2", "session_id": "s2", "ts": 6.0, "quote": "ok merge"}]
    (d / "items.json").write_text(json.dumps(items), encoding="utf-8")
    lab = {mrc.column(r, mrc.LABEL_SYSTEM): {"i1": True, "i2": r == "r1"} for r in ("r1", "r2")}
    (d / "labels.json").write_text(json.dumps(lab), encoding="utf-8")
    notes = {"R1": {"kind": "rule", "ref": "a"}, "R2": {"kind": "rule", "ref": "b"},
             "C1": {"kind": "claude_md", "ref": "CLAUDE.md"}}
    (d / "units.json").write_text(json.dumps({"i1": {"notes": notes}, "i2": {"notes": notes}}), encoding="utf-8")
    verdicts = {mrc.column("r1", mrc.JUDGE_SYSTEM): {"i1": {"notes": ["R1", "R2", "C1"]}, "i2": {"notes": ["R1"]}},
                mrc.column("r2", mrc.JUDGE_SYSTEM): {"i1": {"notes": ["R1"]}, "i2": {"notes": ["R1"]}}}
    (d / "verdicts.json").write_text(json.dumps(verdicts), encoding="utf-8")
    return d


def test_known_corrections_need_both_raters(tmp_path):
    d = _rc_dir(tmp_path)
    assert tool.known_corrections(d, ["r1", "r2"]) == [("a", "s1", 5.0)]


def test_linked_corrections_need_a_real_quote(tmp_path):
    d = _rc_dir(tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r) for r in [
        {"session_id": "s1", "quote": "stop doing x", "contradicts": ["c"]},
        {"session_id": "s2", "quote": "ok merge", "contradicts": ["c"]},
        {"session_id": "s1", "quote": "Stop doing X", "contradicts": []},
    ]) + "\n", encoding="utf-8")
    assert tool.linked_corrections(ledger, d, ["r1", "r2"]) == [("c", "s1", 5.0)]


def test_top_carrier_and_without():
    blocks = {"s1": ["a", "b", "c"], "s2": ["a"]}
    assert tool.top_carrier(UNITS, blocks, tool.BROAD) == "a"  # 2 fresh units; b is redundant
    assert tool.top_carrier(UNITS, {"s1": ["c"], "s2": ["d"]}, tool.BROAD) == "c"  # tie by slug
    assert tool.top_carrier(UNITS, {"s1": ["c"]}, tool.STRICT) is None
    assert tool.without(blocks, "a") == {"s1": ["b", "c"], "s2": []}
