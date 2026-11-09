"""``tools/measure_advisor_pairs.py``: an advised child against one on the
stronger model, over synthetic twin pairs and transcripts (#818)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_advisor_pairs as tool  # noqa: E402

from mnemo.core import twins  # noqa: E402

# --- transcripts ---------------------------------------------------------------


def _assistant(mid: str, model: str, usage: dict, content: Optional[list] = None) -> dict:
    return {"type": "assistant",
            "message": {"id": mid, "model": model, "usage": usage, "content": content or []}}


#: The part 1 probe's advised response (2026-11-09, Claude Code 2.1.321): one
#: Sonnet response whose usage holds two of its own iterations and one Opus
#: advisor iteration, written as several records sharing one id. Claude Code's
#: own `cost-state` priced it at $0.1018243 (sonnet) and $0.24502 (opus).
PROBE_USAGE = {
    "input_tokens": 4, "cache_creation_input_tokens": 22859,
    "cache_read_input_tokens": 92443, "output_tokens": 167,
    "cache_creation": {"ephemeral_1h_input_tokens": 22503, "ephemeral_5m_input_tokens": 0},
    "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
    "iterations": [
        {"input_tokens": 2, "output_tokens": 118, "cache_read_input_tokens": 34970,
         "cache_creation_input_tokens": 22503, "type": "message",
         "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 22503}},
        {"input_tokens": 59150, "output_tokens": 421, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 0, "type": "advisor_message",
         "model": "claude-opus-5-5",
         "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}},
        {"input_tokens": 2, "output_tokens": 49, "cache_read_input_tokens": 57473,
         "cache_creation_input_tokens": 356, "type": "message",
         "cache_creation": {"ephemeral_5m_input_tokens": 356, "ephemeral_1h_input_tokens": 0}},
    ],
}


def _write(path: Path, records: List[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _plain(model: str, *, read: int = 0, out: int = 0, write_1h: int = 0) -> dict:
    return {"input_tokens": 0, "output_tokens": out, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": write_1h,
            "cache_creation": {"ephemeral_1h_input_tokens": write_1h,
                               "ephemeral_5m_input_tokens": 0}}


def _advised(path: Path, *, calls: int = 1, read: int = 1_000_000) -> Path:
    """A Sonnet twin: *calls* probe-shaped advised responses, then plain turns."""
    records = []
    for n in range(calls):
        records.append(_assistant(f"msg_a{n}", "claude-sonnet-5-5", PROBE_USAGE,
                                  [{"type": "server_tool_use", "name": "advisor", "input": {}}]))
        records.append(_assistant(f"msg_a{n}", "claude-sonnet-5-5", PROBE_USAGE,
                                  [{"type": "text", "text": "ok"}]))
    records.append(_assistant("msg_plain", "claude-sonnet-5-5", _plain("s", read=read, out=1000)))
    return _write(path, records)


def _alone(path: Path, *, read: int = 1_000_000, error: str = "") -> Path:
    records = [_assistant("msg_o", "claude-opus-5-5", _plain("o", read=read, out=1000))]
    if error:
        records.append({"type": "assistant", "isApiErrorMessage": True, "error": error,
                        "message": {"model": "<synthetic>", "usage": {}, "content": []}})
    return _write(path, records)


# --- pricing ---------------------------------------------------------------------


def test_it_reproduces_claude_codes_own_cost_for_the_probe_child(tmp_path: Path) -> None:
    """The child at its own rate, the advisor at Opus's, each to the cent of
    what Claude Code recorded — and the repeated record counted once."""
    path = _write(tmp_path / "t.jsonl", [
        _assistant("msg_1", "claude-sonnet-5-5", PROBE_USAGE),
        _assistant("msg_1", "claude-sonnet-5-5", PROBE_USAGE),
    ])
    found = tool.measure_twin(path)

    assert found["own"]["usd"] == pytest.approx(0.1018243, abs=1e-9)
    assert found["advisor"]["usd"] == pytest.approx(0.24502, abs=1e-9)
    assert found["usd"] == pytest.approx(0.1018243 + 0.24502, abs=1e-9)
    assert found["advisor_calls"] == 1
    assert found["advisor"]["tokens"]["input"] == 59150
    assert found["own"]["tokens"] == {"input": 4, "output": 167, "cache_read": 92443,
                                      "cache_write_5m": 356, "cache_write_1h": 22503}


def test_the_top_level_usage_alone_would_lose_the_advisor() -> None:
    """Why iterations are read: the top level is the child's turns only."""
    top = tool.tokens_of(PROBE_USAGE)
    assert top["input"] == 4 and top["output"] == 167


def test_a_model_the_table_does_not_name_is_unpriced_not_guessed(tmp_path: Path) -> None:
    path = _write(tmp_path / "t.jsonl", [
        _assistant("m1", "claude-opus-9-9", _plain("x", read=10, out=10)),
    ])
    found = tool.measure_twin(path)
    assert found["usd"] is None and found["own"]["unpriced"] == ["claude-opus-9-9"]
    assert tool.price("claude-opus-5-5[1m]", tool.tokens_of(_plain("o", out=1_000_000))) \
        == pytest.approx(20.0)


def test_writes_without_a_split_are_priced_as_one_hour_writes() -> None:
    entry = {"cache_creation_input_tokens": 1_000_000}
    assert tool.tokens_of(entry)["cache_write_1h"] == 1_000_000
    assert tool.price("claude-sonnet-5-5", tool.tokens_of(entry)) == pytest.approx(4.0)


def test_subagents_count_toward_the_arm_and_errors_only_from_the_main_one(tmp_path: Path) -> None:
    main = _write(tmp_path / "s1.jsonl", [
        _assistant("m1", "claude-sonnet-5-5", _plain("s", out=1_000_000)),
    ])
    _write(tmp_path / "s1" / "subagents" / "agent-x.jsonl", [
        _assistant("m1", "claude-haiku-5-5", _plain("h", out=1_000_000)),
        {"type": "assistant", "isApiErrorMessage": True, "error": "server_error",
         "message": {"model": "<synthetic>"}},
    ])
    found = tool.measure_twin(main)
    assert found["usd"] == pytest.approx(10.0 + 0.5)
    assert found["api_errors"] == []
    assert tool.measure_twin(tmp_path / "gone.jsonl") is None


def test_web_searches_are_priced_per_request(tmp_path: Path) -> None:
    usage = {**_plain("s"), "server_tool_use": {"web_search_requests": 3}}
    found = tool.measure_twin(_write(tmp_path / "w.jsonl", [
        _assistant("m1", "claude-sonnet-5-5", usage),
    ]))
    assert found["usd"] == pytest.approx(0.03)


# --- the verdict -------------------------------------------------------------------


@pytest.mark.parametrize("cost, speed, word", [
    (0.80, 0.90, "POSITIVE"),    # better on both
    (0.80, 1.14, "POSITIVE"),    # better on one, under 15% worse on the other
    (1.10, 0.70, "POSITIVE"),
    (0.80, 1.16, "NEGATIVE"),    # more than 15% worse
    (1.00, 1.00, "NEGATIVE"),    # better on neither
    (1.05, 1.05, "NEGATIVE"),
    (None, 0.90, "PENDING"),
])
def test_cost_and_speed_decide_once_quality_passes(cost, speed, word) -> None:
    assert tool.verdict(read=6, quality=0.70, cost=cost, speed=speed)[0] == word


def test_quality_is_a_gate_and_six_pairs_are_its_floor() -> None:
    assert tool.verdict(read=6, quality=0.69, cost=0.5, speed=0.5)[0] == "NEGATIVE"
    assert tool.verdict(read=5, quality=1.0, cost=0.5, speed=0.5)[0] == "PENDING"
    assert (tool.MIN_READ, tool.QUALITY_BAR, tool.MARGIN) == (6, 0.70, 0.15)


# --- pairs from the ledger -----------------------------------------------------------


def _arms_pair(vault: Path, pid: str, *, choice_arm: str = "", wall=(600, 600),
               shown: bool = True) -> Dict[str, str]:
    """An arms pair: tag ``<pid>a`` advised (sonnet + opus), ``<pid>b`` alone (opus).

    The order shown is B then A, so a label is never the arm's initial.
    """
    advised, alone = f"{pid[:5]}a", f"{pid[:5]}b"
    twins._append(vault, {
        "event": "pair", "pair": pid, "issue": 7, "repo_root": "/r", "base": "0" * 40,
        "model": "sonnet", "advisor": "opus",
        "twins": [
            {"tag": advised, "tree": f"/r-wt-7-{advised}", "branch": f"fix/issue-7-{advised}",
             "model": "sonnet", "advisor": "opus"},
            {"tag": alone, "tree": f"/r-wt-7-{alone}", "branch": f"fix/issue-7-{alone}",
             "model": "opus", "advisor": None},
        ],
    })
    for n, tag in enumerate((advised, alone)):
        twins._append(vault, {"event": "started", "pair": pid, "tag": tag,
                              "short_id": f"{pid[:4]}000{n}"})
    if shown:
        twins._append(vault, {"event": "shown", "pair": pid, "order": [alone, advised],
                              "metrics": {advised: {"tokens": 1, "wall_seconds": wall[0]},
                                          alone: {"tokens": 1, "wall_seconds": wall[1]}}})
    if choice_arm:
        label = {"advised": "B", "alone": "A", "tie": "tie"}[choice_arm]
        twins._append(vault, {"event": "prefer", "pair": pid, "choice": label})
    return {"advised": advised, "alone": alone}


def _rows(vault: Path, paths: Dict[str, Path], reports: Optional[Dict[str, str]] = None):
    return tool.rows(
        list(twins.read_pairs(vault).values()),
        transcript=lambda pair, twin: paths.get(twin.tag),
        report=lambda path: (reports or {}).get(str(path), ""),
        live=lambda tag: pytest.fail("a shown pair was read live"),
    )


def test_six_pairs_where_the_advised_arm_holds_up_read_positive(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    paths: Dict[str, Path] = {}
    answers = ["advised", "tie", "tie", "advised", "alone", "tie"]
    for n, answer in enumerate(answers):
        tags = _arms_pair(vault, f"{n}aaaaa", choice_arm=answer, wall=(600, 650))
        paths[tags["advised"]] = _advised(tmp_path / f"{n}a.jsonl")
        paths[tags["alone"]] = _alone(tmp_path / f"{n}b.jsonl")

    rows = _rows(vault, paths)
    report = tool.measure(rows)

    assert [r["answer"] for r in rows] == answers
    assert report["read"] == 6 and report["quality"] == pytest.approx(5 / 6)
    # advised: $0.3468443 probe + 1M sonnet reads $0.10 + 1k out $0.01;
    # alone: 1M opus reads $0.20 + 1k out $0.02.
    assert report["cost_ratio"] == pytest.approx((0.1018243 + 0.24502 + 0.11) / 0.22)
    assert report["speed_ratio"] == pytest.approx(600 / 650)
    assert report["advisor_calls"] == {"total": 6, "median": 1}
    assert report["verdict"] == "NEGATIVE"   # cheaper here it is not: cost 2.08x
    assert "cost worse" in report["why"]


def test_the_advised_arm_cheaper_and_no_slower_than_the_margin_is_positive(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    paths: Dict[str, Path] = {}
    for n in range(6):
        tags = _arms_pair(vault, f"{n}bbbbb", choice_arm="tie", wall=(684, 600))
        paths[tags["advised"]] = _advised(tmp_path / f"{n}a.jsonl", read=1_000_000)
        paths[tags["alone"]] = _alone(tmp_path / f"{n}b.jsonl", read=10_000_000)

    report = tool.measure(_rows(vault, paths))
    assert report["cost_ratio"] < 1 and report["speed_ratio"] == pytest.approx(1.14)
    assert (report["verdict"], report["why"]) == ("POSITIVE", "cost better, speed within")


def test_a_stalled_twin_leaves_its_pair_out_of_speed_only(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    paths: Dict[str, Path] = {}
    tags = _arms_pair(vault, "cccccc", choice_arm="tie", wall=(600, 9000))
    paths[tags["advised"]] = _advised(tmp_path / "a.jsonl")
    paths[tags["alone"]] = _alone(tmp_path / "b.jsonl", error="rate_limit")
    tags = _arms_pair(vault, "dddddd", choice_arm="tie", wall=(600, 600))
    paths[tags["advised"]] = _advised(tmp_path / "c.jsonl")
    paths[tags["alone"]] = _alone(tmp_path / "d.jsonl")

    rows = _rows(vault, paths)
    report = tool.measure(rows)
    assert rows[0]["arms"]["alone"]["stalled"] is True
    assert (report["speed_pairs"], report["speed_left_out"]) == (1, 1)
    assert report["speed_ratio"] == pytest.approx(1.0)
    assert report["cost_pairs"] == 2
    assert "stalled: rate_limit" in tool.format_report(report, rows)


def test_a_pair_whose_advised_twin_never_called_is_reported_apart(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    paths: Dict[str, Path] = {}
    tags = _arms_pair(vault, "eeeeee", choice_arm="advised")
    paths[tags["advised"]] = _advised(tmp_path / "a.jsonl", calls=0)
    paths[tags["alone"]] = _alone(tmp_path / "b.jsonl")
    tags = _arms_pair(vault, "ffffff", choice_arm="alone")
    paths[tags["advised"]] = _advised(tmp_path / "c.jsonl", calls=2)
    paths[tags["alone"]] = _alone(tmp_path / "d.jsonl")

    rows = _rows(vault, paths)
    report = tool.measure(rows)
    assert [r["never_called"] for r in rows] == [True, False]
    assert (report["used"], report["never_called"], report["read"]) == (1, 1, 1)
    assert report["answers"] == {"advised": 0, "tie": 0, "alone": 1}
    assert report["advisor_calls"]["total"] == 2
    text = tool.format_report(report, rows)
    assert text.index("never called the advisor") < text.index("eeeeee")


def test_only_pairs_of_two_arms_are_read(tmp_path: Path) -> None:
    """A #449 pair (same arm twice) is measure_child_pairs', not this tool's."""
    vault = tmp_path / "v"
    twins._append(vault, {
        "event": "pair", "pair": "999999", "issue": 9, "repo_root": "/r", "base": "0" * 40,
        "model": "opus", "twins": [{"tag": "99999a", "tree": "/a", "branch": "a"},
                                   {"tag": "99999b", "tree": "/b", "branch": "b"}],
    })
    for tag in ("99999a", "99999b"):
        twins._append(vault, {"event": "started", "pair": "999999", "tag": tag,
                              "short_id": "99990000"})
    assert _rows(vault, {}) == []


def test_a_report_that_names_the_advisor_is_flagged(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    tags = _arms_pair(vault, "gggggg", choice_arm="tie")
    a, b = _advised(tmp_path / "a.jsonl"), _alone(tmp_path / "b.jsonl")
    rows = _rows(vault, {tags["advised"]: a, tags["alone"]: b},
                 reports={str(a): "I consulted the advisor before finishing."})
    report = tool.measure(rows)
    assert rows[0]["arms"]["advised"]["report_names_advisor"] is True
    assert report["report_names_advisor"] == 1
    assert "may not have been blind" in tool.format_report(report, rows)


def test_nothing_read_yet_is_pending_and_says_so(tmp_path: Path) -> None:
    report = tool.measure([])
    assert report["verdict"] == "PENDING" and report["why"] == "0 of 6 pairs read"
    assert "verdict: PENDING" in tool.format_report(report, [])


# --- the price check -------------------------------------------------------------------


def test_check_prices_reproduces_claude_codes_rows() -> None:
    states = [{"modelUsage": {
        # The probe's two rows, as Claude Code recorded them.
        "claude-sonnet-5-5": {"inputTokens": 4, "outputTokens": 167,
                              "cacheReadInputTokens": 92443,
                              "cacheCreationInputTokens": 22859,
                              "cacheCreationEphemeral5mInputTokens": 0,
                              "cacheCreationEphemeral1hInputTokens": 22503,
                              "webSearchRequests": 0, "costUSD": 0.1018243},
        "claude-opus-5-5": {"inputTokens": 59150, "outputTokens": 421,
                            "cacheCreationInputTokens": 0, "costUSD": 0.24502},
        # Two web searches on top of a Haiku row.
        "claude-haiku-5-5": {"inputTokens": 36618, "outputTokens": 4931,
                             "webSearchRequests": 2, "costUSD": 0.0261273},
        "claude-opus-4-8": {"inputTokens": 1, "costUSD": 0.00001},
    }}, {"modelUsage": {"claude-sonnet-5-5": {"outputTokens": 100, "costUSD": 9.0}}}]

    found = tool.check_prices(states)
    assert found["claude-sonnet-5-5"] == {"rows": 2, "reproduced": 1, "unpriced": 0}
    assert found["claude-opus-5-5"]["reproduced"] == 1
    assert found["claude-haiku-5-5"]["reproduced"] == 1
    assert found["claude-opus-4-8"] == {"rows": 1, "reproduced": 0, "unpriced": 1}


def test_main_prints_a_report_json_and_the_price_check(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "v"
    _arms_pair(vault, "hhhhhh", choice_arm="advised")

    assert tool.main(["--vault", str(vault)]) == 0
    out = capsys.readouterr().out
    assert "verdict: PENDING" in out and "provenance" in out

    assert tool.main(["--vault", str(vault), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["report"]["pairs"] == 1 and data["report"]["bar"]["quality"] == 0.70

    projects = tmp_path / "projects"
    _write(projects / "p" / "s.jsonl", [{"type": "cost-state", "modelUsage": {
        "claude-opus-5-5": {"inputTokens": 59150, "outputTokens": 421, "costUSD": 0.24502}}}])
    assert tool.main(["--check-prices", "--projects", str(projects)]) == 0
    assert "claude-opus-5-5: 1 row(s), 1 reproduced" in capsys.readouterr().out
