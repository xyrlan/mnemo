"""``tools/label_recall_pairs.py`` over synthetic judgments and a loopback server.

What is pinned is what makes a hand label worth more than a model's: the rater
never sees a judge's score, an answer is on disk before the next pair is
shown, a second ``--sample`` cannot wipe labels, and the report grades the
score the pair was drawn on rather than whatever the judgments say today.
"""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.parse
import urllib.request
from http.server import HTTPServer
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "label_recall_pairs.py"
_spec = importlib.util.spec_from_file_location("label_recall_pairs", _TOOL)
lrp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lrp)


def _units(per_level=20):
    """Two units whose rules spread evenly over the five strata."""
    units, texts = [], []
    for name in ("a", "b"):
        noul = {"%s-%02d-%d" % (name, i, level): level * 0.2 + 0.1
                for level in range(5) for i in range(per_level)}
        units.append({"project": "p", "topic": "t", "query": "task " + name, "noul": noul, "reads": []})
        texts.append({slug: "rule text of " + slug for slug in noul})
    return units, texts


def test_every_stratum_gives_its_share_and_the_draw_is_deterministic():
    units, texts = _units()
    pairs = lrp.draw(units, texts, per_stratum=4)
    assert len(pairs) == 20
    assert sorted(lrp.stratum(p["first"]) for p in pairs) == sorted(list(range(5)) * 4)
    assert [p["slug"] for p in pairs] == [p["slug"] for p in lrp.draw(units, texts, per_stratum=4)]
    assert [p["id"] for p in pairs] == list(range(20))


def test_a_score_of_one_lands_in_the_last_stratum():
    assert lrp.stratum(1.0) == 4 and lrp.stratum(0.0) == 0 and lrp.stratum(0.8) == 4


def test_a_thin_stratum_gives_what_it_has_and_a_rule_without_text_is_skipped():
    units = [{"project": "p", "topic": "t", "query": "q", "reads": [],
              "noul": {"high": 0.95, "gone": 0.9, "low": 0.05}}]
    pairs = lrp.draw(units, [{"high": "h", "gone": "", "low": "l"}], per_stratum=12)
    assert sorted(p["slug"] for p in pairs) == ["high", "low"]


def test_the_rater_sees_the_task_and_the_rule_and_nothing_a_judge_said():
    units, texts = _units()
    texts = [{slug: "a rule body" for slug in t} for t in texts]
    pair = lrp.draw(units, texts, per_stratum=1)[0]
    assert set(lrp.blind(pair)) == {"id", "task", "rule"}
    page = lrp.render([pair])
    assert pair["task"] in page and pair["rule"] in page
    assert str(pair["first"]) not in page and pair["slug"] not in page


def test_only_a_0_1_2_on_a_known_pair_is_recorded():
    units, texts = _units()
    pairs = lrp.draw(units, texts, per_stratum=1)
    assert not lrp.record(pairs, 0, 3)
    assert not lrp.record(pairs, 0, True)
    assert not lrp.record(pairs, 99, 1)
    assert lrp.record(pairs, 0, 2) and pairs[0]["label"] == 2
    assert lrp.next_pending(pairs)["id"] == 1


def test_an_answer_is_on_disk_before_the_next_pair_is_shown(tmp_path):
    units, texts = _units()
    path = tmp_path / lrp.HUMAN_NAME
    data = {"pairs": lrp.draw(units, texts, per_stratum=1)}
    lrp.save(path, data)
    server = HTTPServer(("127.0.0.1", 0), lrp.make_handler(path, data))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:%d/" % server.server_address[1]
        body = urllib.parse.urlencode({"id": 0, "label": 1}).encode("utf-8")
        page = urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=10).read().decode("utf-8")
        bad = urllib.parse.urlencode({"id": 1, "label": 7}).encode("utf-8")
        urllib.request.urlopen(urllib.request.Request(url, data=bad), timeout=10).read()
    finally:
        server.shutdown()
        server.server_close()
    assert "1 of 5 labelled" in page
    on_disk = json.loads(path.read_text(encoding="utf-8"))["pairs"]
    assert [p["label"] for p in on_disk] == [1, None, None, None, None]
    assert not list(tmp_path.glob("*.tmp"))


def test_a_sample_with_one_label_in_it_is_not_drawn_over(tmp_path):
    path = tmp_path / lrp.HUMAN_NAME
    assert not lrp.holds_labels(path)
    lrp.save(path, {"pairs": [{"id": 0, "label": None}]})
    assert not lrp.holds_labels(path)
    lrp.save(path, {"pairs": [{"id": 0, "label": None}, {"id": 1, "label": 0}]})
    assert lrp.holds_labels(path)


def test_the_page_says_so_when_nothing_is_left():
    assert "Every pair is labelled" in lrp.render([{"id": 0, "task": "t", "rule": "r", "label": 0}])


def test_the_report_grades_the_frozen_score_and_the_second_judge_where_it_answered():
    pairs = [
        {"id": 0, "unit": "u", "slug": "a", "first": 0.9, "label": 2},
        {"id": 1, "unit": "u", "slug": "b", "first": 0.85, "label": 0},
        {"id": 2, "unit": "u", "slug": "c", "first": 0.1, "label": 0},
        {"id": 3, "unit": "u", "slug": "d", "first": 0.1, "label": 2},
        {"id": 4, "unit": "u", "slug": "e", "first": 0.5, "label": None},
    ]
    r = lrp.report(pairs, {"u": {"a": 2, "b": 1, "e": 2}})
    assert (r["labelled"], r["of"]) == (4, 5)
    assert r["first"]["high_but_irrelevant"] == 1 and r["first"]["low_but_should_read"] == 1
    assert r["first"]["auc_should_read"] == 0.625
    assert r["second"]["pairs"] == 2 and r["second"]["exact"] == 1
    assert r["second"]["confusion"]["0->1"] == 1
    assert "labelled 4/5" in lrp.format_report(r)


def test_an_unlabelled_sample_reports_without_dividing_by_nothing():
    r = lrp.report([{"id": 0, "unit": "u", "slug": "a", "first": 0.9, "label": None}], {})
    assert r["first"]["auc_any"] is None
    assert "n/a" in lrp.format_report(r)


def test_each_rater_has_a_file_and_a_model_never_writes_the_humans():
    assert lrp.labels_name("human") == lrp.HUMAN_NAME
    assert lrp.labels_name("Claude Fable/5.1") == "recall-labels-claudefable51.json"
    assert lrp.labels_name("../..") == lrp.HUMAN_NAME
