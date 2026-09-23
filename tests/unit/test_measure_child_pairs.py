"""``tools/measure_child_pairs.py``: run-to-run spread, tie rate and study size
over synthetic twin pairs (#449)."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_child_pairs as tool  # noqa: E402

from mnemo.core import twins  # noqa: E402


# --- the sizing is #439's, to the pair ---------------------------------------


def test_it_reproduces_439s_preference_table() -> None:
    """#439: 54 / 85 / 153 / 348 non-tied; 89 / 142 / 256 / 580 at 40% ties."""
    effects = (0.75, 0.70, 0.65, 0.60)
    assert [tool.sign_test_pairs(e) for e in effects] == [54, 85, 153, 348]
    assert [tool.sign_test_pairs(e, tie_rate=0.4) for e in effects] == [89, 142, 256, 580]


def test_it_reproduces_439s_token_table() -> None:
    """#439: 37 / 95 / 214 pairs to see a 15% cut at run-to-run log-sd .25/.4/.6."""
    assert [tool.token_pairs(s) for s in (0.25, 0.4, 0.6)] == [37, 95, 214]


def test_a_perfect_rater_needs_fewer_pairs_and_all_ties_need_infinitely_many() -> None:
    assert tool.label_accuracy(1.0) == 1.0
    assert tool.sign_test_pairs(0.7, agreement=1.0) < tool.sign_test_pairs(0.7)
    assert tool.sign_test_pairs(0.7, tie_rate=1.0) is None
    with pytest.raises(ValueError):
        tool.label_accuracy(0.5)


# --- the spread ----------------------------------------------------------------


def test_twins_that_agree_exactly_have_no_spread() -> None:
    s = tool.spread([(30000, 30000), (50000, 50000)])
    assert s["log_sd"] == 0.0 and s["median_ratio"] == 1.0


def test_the_spread_is_the_pooled_within_pair_log_sd() -> None:
    """Twins a factor e apart: d = 1 per pair, so sd = sqrt(1/2)."""
    e = math.e
    s = tool.spread([(e * 1000, 1000), (2000, e * 2000), (5, 5 * e)])
    assert s["log_sd"] == pytest.approx(math.sqrt(0.5))
    assert s["median_ratio"] == pytest.approx(e)
    assert s["low"] < s["log_sd"] < s["high"]


def test_six_pairs_leave_a_wide_interval() -> None:
    s = tool.spread([(1.3, 1.0)] * 6)
    assert s["high"] / s["low"] > 2.5


def test_pairs_without_a_value_are_counted_not_used() -> None:
    s = tool.spread([(1000, None), (0, 5), (100, 200)])
    assert (s["pairs"], s["skipped"]) == (1, 2)
    assert tool.spread([])["log_sd"] is None


def test_wilson_bounds_a_tie_rate() -> None:
    low, high = tool.wilson(2, 6)
    assert 0 < low < 2 / 6 < high < 1
    assert tool.wilson(0, 0) == (None, None)


# --- from the ledger ------------------------------------------------------------


def _pair(vault: Path, pid: str, *, tokens, wall, choice: str = "",
          shown: bool = True) -> None:
    tags = [f"{pid[:5]}a", f"{pid[:5]}b"]
    twins._append(vault, {
        "event": "pair", "pair": pid, "issue": 7, "repo_root": "/r", "base": "0" * 40,
        "twins": [{"tag": t, "tree": f"/r-wt-7-{t}", "branch": f"fix/issue-7-{t}"}
                  for t in tags],
    })
    for n, tag in enumerate(tags):
        twins._append(vault, {"event": "started", "pair": pid, "tag": tag,
                              "short_id": f"{pid[:4]}000{n}"})
    if shown:
        twins._append(vault, {
            "event": "shown", "pair": pid, "order": tags,
            "metrics": {t: {"tokens": k, "wall_seconds": w}
                        for t, k, w in zip(tags, tokens, wall)},
        })
    if choice:
        twins._append(vault, {"event": "prefer", "pair": pid, "choice": choice})


def test_it_reads_the_snapshot_show_took(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    _pair(vault, "aaaaaa", tokens=(30000, 30000 * math.e), wall=(600, 600), choice="A")
    _pair(vault, "bbbbbb", tokens=(40000, 40000), wall=(900, 900 * math.e), choice="tie")
    _pair(vault, "cccccc", tokens=(10000, 10000), wall=(60, 60), choice="tie")

    rows = tool.rows(list(twins.read_pairs(vault).values()),
                     live=lambda tag: pytest.fail("a shown pair was read live"))
    report = tool.measure(rows)

    assert report["pairs"] == 3
    assert report["tokens"]["log_sd"] == pytest.approx(math.sqrt(1 / 6))
    assert report["wall"]["log_sd"] == pytest.approx(math.sqrt(1 / 6))
    assert (report["ties"], report["answered"]) == (2, 3)
    assert report["tie_rate"] == pytest.approx(2 / 3)
    assert report["preference_pairs"]["point"]["70/30"] == round(
        tool.sign_test_pairs_raw(0.7) / (1 / 3))
    assert report["token_pairs"]["point"] == tool.token_pairs(math.sqrt(1 / 6))


def test_a_pair_never_shown_is_read_live_and_a_lone_twin_is_left_out(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    _pair(vault, "dddddd", tokens=(0, 0), wall=(0, 0), shown=False)
    twins._append(vault, {
        "event": "pair", "pair": "eeeeee", "issue": 8, "repo_root": "/r", "base": "0" * 40,
        "twins": [{"tag": "eeeee1", "tree": "/t1", "branch": "b1"},
                  {"tag": "eeeee2", "tree": "/t2", "branch": "b2"}],
    })
    twins._append(vault, {"event": "started", "pair": "eeeeee", "tag": "eeeee2",
                          "error": "claude --bg failed"})

    seen = []

    def _live(tag):
        seen.append(tag)
        return {"tokens": 20000 if tag.endswith("a") else 40000, "wall_seconds": 300.0}

    rows = tool.rows(list(twins.read_pairs(vault).values()), live=_live)
    assert [r["pair"] for r in rows] == ["dddddd"]
    assert rows[0]["tokens"] == [20000, 40000]
    assert sorted(set(seen)) == ["ddddda", "dddddb"]
    assert rows[0]["choice"] == ""


def test_no_answers_sizes_nothing_it_did_not_measure(tmp_path: Path) -> None:
    report = tool.measure([])
    assert report["tie_rate"] is None
    assert report["preference_pairs"]["point"]["60/40"] is None
    assert report["non_tied_pairs"]["60/40"] == 348
    assert report["token_pairs"]["point"] is None
    assert "—" in tool.format_report(report)


def test_main_prints_a_report_and_json(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "v"
    _pair(vault, "aaaaaa", tokens=(30000, 45000), wall=(600, 840), choice="B")

    assert tool.main(["--vault", str(vault), "--list"]) == 0
    out = capsys.readouterr().out
    assert "run-to-run log-sd" in out and "aaaaaa" in out and "70/30" in out

    assert tool.main(["--vault", str(vault), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["report"]["tokens"]["log_sd"] == pytest.approx(
        abs(math.log(30000 / 45000)) / math.sqrt(2))
