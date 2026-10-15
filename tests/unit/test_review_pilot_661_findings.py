"""The #661 pilot report's numbers rebuild from the data committed beside it (#682).

``design/findings/2026-10-15-review-pilot-661-judges.json`` holds each pair's
two judge verdicts (one per order) and the answer the twins log kept. The
judges give 4 of 8; the log's first answers give the 5 of 8 that
``tools/measure_review_pilot.py`` prints, and a9b631 is the one difference.
"""
from __future__ import annotations

import json
from pathlib import Path

DATA = (Path(__file__).resolve().parents[2] / "design" / "findings"
        / "2026-10-15-review-pilot-661-judges.json")


def _winners(rows, pick):
    out = {}
    for row in rows:
        label = pick(row)
        out[row["pair"]] = "tie" if label == "tie" else row["shown"][label]
    return out


def _judged(row):
    first, second = (j["verdict_shown"] for j in row["judges"])
    return first if first == second else "tie"


def test_both_orders_were_judged_for_every_pair_and_each_shows_both_arms() -> None:
    rows = json.loads(DATA.read_text(encoding="utf-8"))["pairs"]
    assert len(rows) == 10
    for row in rows:
        assert [j["order"] for j in row["judges"]] == [1, 2]
        assert sorted(row["shown"].values()) == ["control", "review"]


def test_the_judges_give_four_of_eight_and_the_log_five() -> None:
    rows = json.loads(DATA.read_text(encoding="utf-8"))["pairs"]
    judged = _winners(rows, _judged)
    logged = _winners(rows, lambda r: r["logged_choice"])

    def count(w):
        return (sum(v == "review" for v in w.values()),
                sum(v == "control" for v in w.values()),
                sum(v == "tie" for v in w.values()))

    assert count(judged) == (4, 4, 2)
    assert count(logged) == (5, 3, 2)
    assert {p for p in judged if judged[p] != logged[p]} == {"a9b631"}
