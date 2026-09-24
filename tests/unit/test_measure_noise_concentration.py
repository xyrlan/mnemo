"""``tools/measure_noise_concentration.py``: do a few rules cause most of the reflex's noise? (#480)

Over synthetic corpora and pages only. What these pin: noise is label 0 and
an unlabelled pair counts as nothing, the top-k ranking and its ties, the
held-out row never measures on the part it picked on, the rated set is one
row per distinct text and reaches every slug it stands for, the rater sees
the page's view and nothing about the corpus, "both G/N" needs both raters,
and every signal is read from what is on disk.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from mnemo.core.text_utils import GRAPH_SECTION_MARKER

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_noise_concentration.py"
_spec = importlib.util.spec_from_file_location("measure_noise_concentration", _TOOL)
mn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mn)

P = mn.pair


def _pairs(spec):
    """``[(slug, label, part), ...]`` -> pairs with one uid each."""
    return [P("u%d" % i, slug, label, part=part) for i, (slug, label, part) in enumerate(spec)]


def test_noise_is_zero_and_unlabelled_counts_as_nothing():
    pairs = _pairs([("a", 0, None), ("a", 1, None), ("a", 2, None), ("a", None, None)])
    assert mn.tally(pairs)["a"] == {"n": 4, "noise": 1, "marginal": 1, "on_point": 1,
                                    "unlabelled": 1}
    k = mn.concentration(pairs)
    assert (k["pairs"], k["labelled"], k["noise"], k["on_point"]) == (4, 3, 1, 1)


def test_ranking_is_by_noise_then_slug_and_skips_rules_never_noisy():
    pairs = _pairs([("b", 0, None)] * 2 + [("a", 0, None)] * 2 + [("c", 0, None)]
                   + [("d", 2, None)] * 5)
    assert mn.ranked_noisy(pairs) == ["a", "b", "c"]
    # Ranked by noise, not by how often injected: "busy" is on-point mostly.
    busy = _pairs([("busy", 0, None)] + [("busy", 2, None)] * 5 + [("junk", 0, None)] * 2)
    assert mn.ranked_noisy(busy) == ["junk", "busy"]


def test_concentration_top_k_shares_noise_and_on_point():
    pairs = _pairs([("gen", 0, None)] * 6 + [("gen", 2, None)]
                   + [("x%d" % i, 0, None) for i in range(4)]
                   + [("good", 2, None)] * 3)
    k = mn.concentration(pairs, tops=(1, 5))
    assert k["rules_injected"] == 6 and k["rules_noisy"] == 5
    top1 = k["top"]["1"]
    assert (top1["noise"], top1["noise_of"], top1["on_point"], top1["on_point_of"]) == (6, 10, 1, 4)
    assert top1["noise_share"] == 0.6 and top1["on_point_share"] == 0.25
    assert k["top"]["5"]["noise_share"] == 1.0


def test_held_out_picks_on_one_part_and_measures_only_the_other():
    pairs = _pairs([("a", 0, "dev")] * 3 + [("b", 0, "dev")]
                   + [("b", 0, "test")] * 4 + [("a", 0, "test")] + [("a", 2, "test")])
    h = mn.held_out(pairs, "dev", "test", tops=(1,))
    top1 = h["top"]["1"]
    # "a" won on dev; on test it made 1 of 5 noise and the one on-point.
    assert (top1["noise"], top1["noise_of"], top1["on_point"]) == (1, 5, 1)
    assert (h["pick_pairs"], h["measure_pairs"]) == (4, 6)
    assert mn.held_out(pairs, "dev", "nowhere") is None


def test_most_emitted_reads_counts_only_with_slug_ties():
    assert mn.most_emitted({"b": 3, "a": 3, "c": 9, "d": 1}, 3) == ["c", "a", "b"]


def test_rated_set_is_one_row_per_text_and_keeps_every_slug():
    rows = mn.rated_set({
        "day-one c": [("x", "Same text.", False), ("y", "Other.", False)],
        "day-one ac": [("x", "Same text.", False)],
        "mature #411": [("gone", "Frozen.", True)],
    })
    assert len(rows) == 3
    same = next(r for r in rows if r["text"] == "Same text.")
    assert sorted(map(tuple, same["slugs"])) == [("day-one ac", "x"), ("day-one c", "x")]
    assert next(r for r in rows if r["text"] == "Frozen.")["fallback"] is True
    assert same["id"] == mn.text_id("Same text.")


def test_the_rater_sees_the_view_and_nothing_about_the_corpus():
    (row,) = mn.rated_set({"day-one b": [("report-status", "Report status. Body.", False)]})
    seen = mn.mdk.rater_prompt([row])
    assert "Report status. Body." in seen
    for leak in ("day-one", "report-status", "noise", "injected"):
        assert leak not in seen


def test_plan_batches_both_raters_the_second_in_reverse_and_skips_done():
    rated = [{"id": "r%02d" % i, "text": "t"} for i in range(20)]
    todo = mn.plan(rated, {}, size=15)
    assert [(m, len(b)) for m, b in todo] == [(mn.mdk.RATERS[0], 15), (mn.mdk.RATERS[0], 5),
                                              (mn.mdk.RATERS[1], 15), (mn.mdk.RATERS[1], 5)]
    assert todo[2][1][0]["id"] == "r19"
    col = mn.mdk.column(mn.mdk.RATERS[0])
    done = {col: {"r%02d" % i: "G" for i in range(20)}}
    assert [m for m, _ in mn.plan(rated, done, size=15)] == [mn.mdk.RATERS[1]] * 2


def test_the_worst_case_rated_set_fits_the_issues_budget():
    # Five corpora, top 20 each, no two sharing a text: every call still fits.
    worst = [{"id": "r%03d" % i, "text": "t"} for i in range(5 * mn.RATED_TOP)]
    assert mn.MAX_CALLS == 15
    assert len(mn.plan(worst, {})) <= mn.MAX_CALLS


def test_both_gn_needs_both_raters_and_either_needs_one():
    rated = [{"id": "g", "slugs": [["c", "gen"]]}, {"id": "m", "slugs": [["c", "mixed"]]},
             {"id": "h", "slugs": [["c", "half"]]}, {"id": "s", "slugs": [["c", "sys"]]}]
    labels = {"A": {"g": "G", "m": "N", "h": "G", "s": "S"},
              "B": {"g": "N", "m": "T", "s": "W"}}
    v = mn.verdicts(rated, labels, ["A", "B"])
    assert v[("c", "gen")]["both_gn"] is True
    assert v[("c", "mixed")]["both_gn"] is False and v[("c", "mixed")]["either_gn"] is True
    assert v[("c", "half")]["complete"] is False and v[("c", "half")]["both_gn"] is False
    assert v[("c", "sys")]["either_gn"] is False  # W is wrong, not generic


def test_kind_rows_attribute_noise_to_both_gn_rules():
    pairs = _pairs([("gen", 0, None)] * 3 + [("sys", 0, None)] + [("sys", 2, None)])
    v = {("c", "gen"): {"letters": ["G", "G"], "complete": True, "both_gn": True, "either_gn": True},
         ("c", "sys"): {"letters": ["S", "G"], "complete": True, "both_gn": False, "either_gn": True}}
    k = mn.kind_rows("c", pairs, ["gen", "sys"], v)
    assert (k["rated"], k["both_gn_rules"]) == (2, 1)
    assert (k["both_gn"]["noise"], k["both_gn"]["noise_of"], k["both_gn"]["on_point"]) == (3, 4, 0)
    assert (k["either_gn"]["noise"], k["either_gn"]["on_point"]) == (4, 1)


def test_signal_row_counts_flags_on_the_top_and_what_flagged_rules_made():
    pairs = _pairs([("a", 0, None)] * 2 + [("b", 2, None)] + [("c", 0, None)])
    assert mn.signal_row(pairs, ["a", "c"], None) is None
    s = mn.signal_row(pairs, ["a", "c"], {"a", "b", "never-injected"})
    assert (s["flags_top"], s["top"], s["flags_injected"]) == (1, 2, 2)
    assert (s["noise"], s["noise_of"], s["on_point"], s["on_point_of"]) == (2, 3, 1, 1)


# --- the corpora, from files shaped like the real ones ---------------------------

def _unit(uid, slugs):
    return {"uid": uid, "pool": [{"slug": s} for s in slugs]}


def test_day_one_arm_b_splits_at_install_after_and_reads_the_labels(tmp_path: Path):
    progress = {
        "meta": {"install_after": 1},
        "b": {"rows": [{"sid": "s1", "units": [_unit("u1", ["a"]), _unit("u2", [])]},
                       {"sid": "s2", "units": [_unit("u3", ["a", "b"])]}]},
        "c": {"units": [_unit("u4", ["c"])], "first_turns": [_unit("u5", ["c"])]},
    }
    (tmp_path / "progress.json").write_text(json.dumps(progress), encoding="utf-8")
    (tmp_path / "labels.json").write_text(json.dumps(
        {"sonnet@x": {"u1": {"a": 0}, "u3": {"a": 0, "b": 2}, "u4": {"c": 1}}}), encoding="utf-8")
    got = mn.day_one_corpora(tmp_path)
    b = got["day-one b"]["pairs"]
    assert [(p["slug"], p["label"], p["part"]) for p in b] == [
        ("a", 0, "history"), ("a", 0, "future"), ("b", 2, "future")]
    assert got["day-one b"]["split"] == ("history", "future")
    assert [(p["slug"], p["label"]) for p in got["day-one c"]["pairs"]] == [("c", 1), ("c", None)]
    assert "day-one ac" not in got


def test_mature_455_counts_only_what_the_session_replay_injected(tmp_vault: Path):
    meta = tmp_vault / ".mnemo" / "reflex-reach"
    meta.mkdir(parents=True)
    unit = {"uid": "0a", "session_id": "s", "pool": [{"slug": "x", "text": "X."},
                                                     {"slug": "y", "text": "Y."}],
            "shipped": ["x", "y"], "fate": {"x": "injected", "y": "deduped"}}
    (meta / "pools.json").write_text(json.dumps({"units": [unit]}), encoding="utf-8")
    (meta / "labels.json").write_text(json.dumps({"sonnet@x": {"0a": {"x": 0, "y": 0}}}),
                                      encoding="utf-8")
    got = mn.mature_corpora(tmp_vault)
    assert "mature #411" not in got
    (p,) = got["mature #455"]["pairs"]
    assert (p["slug"], p["label"], p["part"]) == ("x", 0, mn.mrg.part_of("0a"))
    assert got["mature #455"]["texts"] == {"x": "X.", "y": "Y."}


# --- pages and signals -----------------------------------------------------------

def _page(path: Path, name: str, body: str, extra: str = "", page_type: str = "feedback") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: %s\ndescription: d\ntype: %s\n%s---\n\n%s\n\n%s\n## Sources\n- [[bots/x]]\n"
        % (name, page_type, extra, body, GRAPH_SECTION_MARKER), encoding="utf-8")
    return path


def _index(vault: Path, rules):
    (vault / ".mnemo").mkdir(parents=True, exist_ok=True)
    (vault / ".mnemo" / "rule-activation-index.json").write_text(
        json.dumps({"rules": rules}), encoding="utf-8")


def test_a_live_page_is_unstamped_but_its_staged_twin_is_read(tmp_vault: Path):
    live = _page(tmp_vault / "shared" / "feedback" / "tokens.md", "Tokens leak", "Rotate them.")
    _page(tmp_vault / "shared" / "_inbox" / "reference" / "tokens.md", "Tokens leak", "x",
          extra="reference_gate: generic\n", page_type="reference")
    _page(tmp_vault / "shared" / "feedback" / "tokens.proposed.md", "Tokens leak", "x",
          extra="reference_gate: system\n")
    _index(tmp_vault, {"tokens": {"type": "feedback", "file_stem": "tokens"}})
    assert mn.page_of(tmp_vault, "tokens") == live
    assert mn.page_of(tmp_vault, "unknown") is None
    f = mn.page_facts(live)
    assert f["stamp"] is None and f["twin_stamps"] == ["generic", "system"]
    assert f["view"] == "Tokens leak. Rotate them."
    assert f["type"] == "feedback"
    assert mn.page_facts(None) == {}


def test_friction_generic_sample_and_receipts_are_read_from_disk(tmp_vault: Path):
    meta = tmp_vault / ".mnemo"
    meta.mkdir(parents=True, exist_ok=True)
    assert mn.friction_flags(tmp_vault) is None
    assert mn.generic_sample(tmp_vault) is None
    (meta / "friction-ledger.jsonl").write_text(
        json.dumps({"contradicts": ["a"]}) + "\nnot json\n" + json.dumps({"contradicts": []}) + "\n",
        encoding="utf-8")
    assert mn.friction_flags(tmp_vault) == {"a"}
    (meta / "generic-sample.json").write_text(json.dumps(
        {"rules": [{"id": "1", "slug": "g"}, {"id": "2", "slug": "s"}]}), encoding="utf-8")
    gs = mn.generic_sample(tmp_vault)
    assert gs == {"sampled": {"g", "s"}, "generic": set(), "raters": []}
    (meta / "generic-labels-r.json").write_text(json.dumps({"labels": {"1": 0, "2": 2}}),
                                                encoding="utf-8")
    assert mn.generic_sample(tmp_vault)["generic"] == {"g"}
    (meta / "reflex-log.jsonl").write_text(
        json.dumps({"emitted": ["a", "b"]}) + "\n" + json.dumps({"emitted": []}) + "\n",
        encoding="utf-8")
    (meta / "reflex-log.jsonl.1").write_text(json.dumps({"emitted": ["a"]}) + "\n",
                                             encoding="utf-8")
    assert mn.receipt_counts(tmp_vault) == {"a": 2, "b": 1}


def test_build_and_report_run_end_to_end():
    pairs = _pairs([("gen", 0, "dev")] * 3 + [("gen", 0, "test")] + [("sys", 2, "test")])
    corpora = {"c": {"pairs": pairs, "split": ("dev", "test")}}
    rated = [{"id": "g1", "text": "t", "slugs": [["c", "gen"]], "fallback": False}]
    cols = [mn.mdk.column(m) for m in mn.mdk.RATERS]
    labels = {cols[0]: {"g1": "G"}, cols[1]: {"g1": "N"}}
    signals = {"c": {"friction contradicts": None, "stamp": {"gen"}}}
    facts = {("c", "gen"): {"type": "feedback", "stamp": None}}
    data = mn.build(corpora, rated, labels, signals, facts)
    c = data["corpora"]["c"]
    assert c["kinds"]["both_gn"]["noise_share"] == 1.0
    assert c["held_out"]["top"]["5"]["noise"] == 1
    assert c["signals"]["friction contradicts"] is None
    assert c["signals"]["stamp"]["flags_top"] == 1
    assert c["rules"][0]["letters"] == ["G", "N"]
    assert data["raters"]["labelled_by_both"] == 1
    text = "\n".join(mn.report_lines(data))
    assert "== c:" in text and "n/a here" in text and "G/N" in text
