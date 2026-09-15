"""What fills each session's context, per tool (#308).

``breakdown_image_79cf6060.jsonl`` is 17 consecutive real events from a
clubinho session, cut on 2026-09-15: every ``usage`` block, id and event kept
verbatim; text, tool inputs and paths replaced by ``[redacted]``; tool-result
text replaced by as many ``x`` (its length is what gets counted); and the
screenshot's base64 cut to its first 32 characters, which still hold the PNG
header. In it a ``Read`` returns a 1440×1000 screenshot, and the next turn's
input grows by exactly 1,891 tokens over the last turn's input and output.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from mnemo.core.activity.context import CHARS_PER_TOKEN, ContextMeter, measure
from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.render import NOTABLE_SHARE, render_queue

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "transcripts"
IMAGE = FIXTURES / "breakdown_image_79cf6060.jsonl"

#: The screenshot's base64 length on disk before the cut: 445,152 characters.
#: ``/context`` sizes a block at ``len(JSON.stringify(block)) / 4``.
IMAGE_B64_CHARS = 445_152
IMAGE_GREW_CONTEXT_BY = 162_286 - 160_089 - 306  # next input − this input − this output


# --- the estimate -------------------------------------------------------------

def test_a_screenshot_costs_what_the_context_grew_not_its_base64():
    size, tools = measure(str(IMAGE))

    assert size == 162_286
    assert tools["Read"] == IMAGE_GREW_CONTEXT_BY == 1891
    assert IMAGE_B64_CHARS // 4 == 111_288  # what chars/4 would have said: 59x


def test_text_counts_at_the_measured_divisor(tmp_path):
    path = _write(tmp_path, [
        _turn("m1", 50_000),
        _use("m1", "t1", "Bash"),
        _result("t1", "x" * 9000),
        _turn("m2", 60_000),  # grew by far more than the result: nothing to cap
    ])

    _, tools = measure(str(path))

    assert tools == {"Bash": round(9000 / CHARS_PER_TOKEN)} == {"Bash": 4000}


def test_a_turn_never_credits_more_than_it_grew(tmp_path):
    path = _write(tmp_path, [
        _turn("m1", 50_000, output=100),
        _use("m1", "t1", "Bash"),
        _use("m1", "t2", "Grep"),
        _result("t1", "x" * 3000),  # estimated 1,333
        _result("t2", "x" * 1500),  # estimated 667
        _turn("m2", 51_100),        # grew by 1,000: both scaled by half
    ])

    _, tools = measure(str(path))

    assert tools == {"Bash": 667, "Grep": 333}


def test_parallel_results_between_split_events_count_once(tmp_path):
    """One response is written as one event per block; results land between them."""
    path = _write(tmp_path, [
        _turn("m1", 50_000),
        _use("m1", "t1", "Read"),
        _result("t1", "x" * 2250),
        _use("m1", "t2", "Bash"),  # same message id: not a new turn
        _result("t2", "x" * 4500),
        _turn("m2", 90_000),
    ])

    _, tools = measure(str(path))

    assert tools == {"Bash": 2000, "Read": 1000}


def test_results_wait_for_the_turn_that_reads_them(tmp_path):
    """Neither a result after the last turn nor a synthetic turn is in the context."""
    path = _write(tmp_path, [
        _turn("m1", 50_000),
        _use("m1", "t1", "Bash"),
        _result("t1", "x" * 4500),
        _synthetic(),
    ])

    assert measure(str(path)) == (50_000, {})

    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_turn("m2", 90_000)) + "\n")

    assert measure(str(path)) == (90_000, {"Bash": 2000})


def test_the_watch_cache_matches_a_cold_read(tmp_path):
    path = tmp_path / "t.jsonl"
    lines = IMAGE.read_text(encoding="utf-8").splitlines(keepends=True)
    cache: dict = {}
    path.write_text("", encoding="utf-8")
    for line in lines:  # one appended line per tick
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        measure(str(path), cache)

    assert measure(str(path), cache) == measure(str(path)) == (162_286, {"Read": 1891, "Bash": 130})


def test_a_rewritten_transcript_starts_the_sums_over(tmp_path):
    path = tmp_path / "t.jsonl"
    shutil.copyfile(IMAGE, path)
    cache: dict = {}
    assert measure(str(path), cache)[1]

    path.write_text(json.dumps(_turn("m9", 40_000)) + "\n", encoding="utf-8")

    assert measure(str(path), cache) == (40_000, {})


def test_malformed_events_cost_nothing():
    meter = ContextMeter()
    for event in (None, [], {"type": "user"}, {"type": "user", "message": {"content": "hi"}},
                  {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
                  {"type": "assistant", "message": {"content": [{"type": "tool_use"}]}}):
        meter.feed(event)

    assert meter.context is None and meter.breakdown() == {}


# --- the row ------------------------------------------------------------------

def _session(**kw) -> Session:
    base = dict(short_id="c1", state="working", tempo="active", name="child")
    base.update(kw)
    return Session(**base)


def _row(out: str) -> str:
    return next(line for line in out.splitlines() if line.startswith("  c1 "))


def test_a_dominant_tool_ends_the_row():
    s = _session(context_tokens=160_000, context_breakdown={"Bash": 74_000, "Read": 1_000})

    assert _row(render_queue([s])).endswith("160k  Bash 46%")


def test_a_share_below_the_bar_is_silent():
    below = int(160_000 * NOTABLE_SHARE) - 1
    s = _session(context_tokens=160_000, context_breakdown={"Bash": below})

    assert _row(render_queue([s])).endswith("160k")


def test_mcp_tools_are_weighed_by_server():
    s = _session(context_tokens=100_000, context_breakdown={
        "mcp__claude-in-chrome__computer": 25_000,
        "mcp__claude-in-chrome__browser_batch": 20_000,
        "Bash": 30_000,
    })

    assert _row(render_queue([s])).endswith("100k  mcp:claude-in-chrome 45%")


def test_no_size_no_suffix():
    s = _session(context_breakdown={"Bash": 90_000})

    assert "Bash" not in _row(render_queue([s]))


def test_a_finished_row_keeps_exploration_then_the_share():
    from mnemo.core.activity.exploration import Exploration

    s = _session(state="done", tempo="idle", context_tokens=100_000, context_breakdown={"Read": 60_000})
    out = render_queue([s], explorations={"c1": Exploration(uses=3, tokens=2_000, reached=True)})

    assert _row(out).endswith("100k  3u/+2k  Read 60%")


def test_json_carries_the_breakdown(monkeypatch, capsys):
    from mnemo.cli.commands import sessions as sessions_cmd

    found = [Session(short_id="79cf6060", state="done", tempo="idle", link_scan_path=str(IMAGE)),
             Session(short_id="nopath00", state="done", tempo="idle")]
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda root=None, *, cwd=None: found)
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep",
                        lambda sessions, *, vault_root: 0)

    assert sessions_cmd.cmd_sessions(argparse.Namespace(json=True, watch=False, **{"all": True})) == 0
    rows = {r["short_id"]: r for r in json.loads(capsys.readouterr().out)}

    assert rows["79cf6060"]["context_tokens"] == 162_286
    assert rows["79cf6060"]["context_breakdown"] == {"Read": 1891, "Bash": 130}
    assert rows["nopath00"]["context_breakdown"] is None


# --- builders -----------------------------------------------------------------

def _turn(message_id: str, context: int, output: int = 0) -> dict:
    return {"type": "assistant", "message": {
        "id": message_id, "model": "claude-opus-5", "content": [{"type": "text", "text": "."}],
        "usage": {"input_tokens": 2, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": context - 2, "output_tokens": output}}}


def _use(message_id: str, tool_id: str, name: str) -> dict:
    event = _turn(message_id, 0)
    event["message"]["content"] = [{"type": "tool_use", "id": tool_id, "name": name, "input": {}}]
    return event


def _result(tool_id: str, text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": text}]}}


def _synthetic() -> dict:
    return {"type": "assistant", "message": {
        "id": "synthetic", "model": "<synthetic>", "content": [{"type": "text", "text": "error"}],
        "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0, "output_tokens": 0}}}


def _write(tmp_path: Path, events: list) -> Path:
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path
