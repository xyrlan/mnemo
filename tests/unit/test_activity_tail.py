"""The tail reader: bytes in, events out, offset threaded.

Every test writes a real file. The reader's whole job is byte offsets and
partial lines, and a mock would only assert that the mock works.
"""
from __future__ import annotations

import json

from mnemo.core.activity.tail import read_tail


def _line(**fields) -> str:
    return json.dumps(fields) + "\n"


def test_cold_start_reads_whole_file_when_small(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_line(type="assistant", n=1) + _line(type="assistant", n=2))

    events, offset = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [1, 2]
    assert offset == p.stat().st_size


def test_second_read_returns_only_the_delta(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_line(type="assistant", n=1))
    _, offset = read_tail(str(p), 0)

    with p.open("a") as fh:
        fh.write(_line(type="assistant", n=2))
    events, new_offset = read_tail(str(p), offset)

    assert [e["n"] for e in events] == [2]
    assert new_offset == p.stat().st_size


def test_nothing_new_returns_empty_and_same_offset(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_line(type="assistant", n=1))
    _, offset = read_tail(str(p), 0)

    events, new_offset = read_tail(str(p), offset)

    assert events == []
    assert new_offset == offset


def test_partial_trailing_line_is_not_consumed(tmp_path):
    """A child mid-write must not cost us the line it is writing."""
    p = tmp_path / "t.jsonl"
    p.write_text(_line(type="assistant", n=1) + '{"type": "assistant", "n": 2')

    events, offset = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [1]
    assert offset == len(_line(type="assistant", n=1).encode())


def test_completed_line_arrives_on_the_next_read(tmp_path):
    p = tmp_path / "t.jsonl"
    first = _line(type="assistant", n=1)
    p.write_text(first + '{"type": "assistant", "n": 2')
    _, offset = read_tail(str(p), 0)

    with p.open("a") as fh:
        fh.write("}\n")
    events, _ = read_tail(str(p), offset)

    assert [e["n"] for e in events] == [2]


def test_cold_start_windows_a_large_file_and_drops_the_partial_head(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("".join(_line(type="assistant", n=i, pad="x" * 200) for i in range(400)))
    size = p.stat().st_size
    assert size > 4096, "fixture must exceed the window for this to mean anything"

    events, offset = read_tail(str(p), 0, window=4096)

    assert events, "window must yield something"
    assert len(events) < 400, "window must not have read the whole file"
    assert offset == size
    assert all("n" in e for e in events), "a truncated head line must be dropped"
    assert events[-1]["n"] == 399, "the window is the tail, not the head"


def test_truncated_file_resets_and_rereads(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("".join(_line(type="assistant", n=i) for i in range(10)))
    _, offset = read_tail(str(p), 0)

    p.write_text(_line(type="assistant", n=99))
    events, new_offset = read_tail(str(p), offset)

    assert [e["n"] for e in events] == [99]
    assert new_offset == p.stat().st_size


def test_invalid_json_line_is_skipped(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_line(type="assistant", n=1) + "not json\n" + _line(type="assistant", n=2))

    events, _ = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [1, 2]


def test_non_object_json_line_is_skipped(tmp_path):
    """Valid JSON that is not a dict would break every consumer downstream."""
    p = tmp_path / "t.jsonl"
    p.write_text(_line(type="assistant", n=1) + "[1, 2, 3]\n" + '"a string"\n')

    events, _ = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [1]


def test_missing_file_returns_empty(tmp_path):
    events, offset = read_tail(str(tmp_path / "nope.jsonl"), 0)

    assert events == []
    assert offset == 0


def test_empty_file_returns_empty(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("")

    events, offset = read_tail(str(p), 0)

    assert events == []
    assert offset == 0


def test_directory_in_place_of_file_returns_empty(tmp_path):
    events, offset = read_tail(str(tmp_path), 0)

    assert events == []
    assert offset == 0
