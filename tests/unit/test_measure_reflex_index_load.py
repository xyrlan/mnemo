"""``tools/measure_reflex_index_load.py``: v1 against v2 index size and load
time (#779), over a small synthetic index."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_reflex_index_load as tool  # noqa: E402


def test_the_synthetic_index_has_the_asked_sizes():
    index = tool.synthetic_index(docs=40, terms=500, postings=2000)
    assert len(index["docs"]) == index["doc_count"] == 40
    assert len(index["postings"]) == 500
    postings = sum(len(e) for e in index["postings"].values())
    assert 1000 <= postings <= 2500
    assert tool.synthetic_index(40, 500, 2000) == index  # seeded


def test_measure_reports_both_versions_and_their_agreement():
    report = tool.measure(tool.synthetic_index(40, 500, 2000), repeats=1)
    assert report["same_scores"] is True
    assert report["v2"]["bytes"] < report["v1"]["bytes"] / 5
    for v in ("v1", "v2"):
        assert set(report[v]) == {"bytes", "load_s", "decide_s", "write_s", "accepted"}
    assert report["bytes_ratio"] == report["v2"]["bytes"] / report["v1"]["bytes"]


def test_write_cost_takes_the_v2_write_apart():
    index = tool.synthetic_index(40, 500, 2000)
    cost = tool.write_cost(index, repeats=1)
    assert cost["index_bytes"] == len(tool.reflex_index.encode_index(index))
    assert cost["prev_bytes"] == cost["index_bytes"], ".prev keeps one whole version"
    assert cost["flush_s"] == cost["durable_s"] - cost["plain_s"]
    assert min(cost["plain_s"], cost["durable_s"], cost["kept_s"]) > 0


def test_the_prompt_is_made_of_terms_the_index_has():
    index = tool.synthetic_index(40, 500, 2000)
    words = tool.prompt_for(index).split()
    assert sum(w in index["postings"] for w in words) >= 4


def test_main_prints_a_report(capsys):
    assert tool.main(["--docs", "20", "--terms", "200", "--postings", "600",
                      "--repeats", "1"]) == 0
    out = capsys.readouterr().out
    assert "v1:" in out and "v2:" in out and "same scores: True" in out
    assert "v2 write:" in out and ".prev holds" in out
