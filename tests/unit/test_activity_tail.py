"""The tail reader: bytes in, events out, offset threaded.

Every test writes a real file. The reader's whole job is byte offsets and
partial lines, and a mock would only assert that the mock works.
"""
from __future__ import annotations

import json
import os

from mnemo.core.activity.tail import read_tail


def _line(**fields) -> str:
    return json.dumps(fields) + "\n"


def _write(path, text: str) -> None:
    """Write *text* with LF endings, whatever the platform.

    `Path.write_text` translates "\n" to "\r\n" on Windows, which adds a
    byte per line and makes every offset assertion here off by the line count
    (CI: `assert 31 == 30`). Claude Code writes transcripts with bare LF, so
    the fixture has to as well — the reader was right both times; the fixture's
    size arithmetic was what broke.
    """
    path.write_bytes(text.encode("utf-8"))


def test_cold_start_reads_whole_file_when_small(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(p, _line(type="assistant", n=1) + _line(type="assistant", n=2))

    events, offset = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [1, 2]
    assert offset == p.stat().st_size


def test_second_read_returns_only_the_delta(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(p, _line(type="assistant", n=1))
    _, offset = read_tail(str(p), 0)

    with p.open("ab") as fh:
        fh.write((_line(type="assistant", n=2)).encode())
    events, new_offset = read_tail(str(p), offset)

    assert [e["n"] for e in events] == [2]
    assert new_offset == p.stat().st_size


def test_nothing_new_returns_empty_and_same_offset(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(p, _line(type="assistant", n=1))
    _, offset = read_tail(str(p), 0)

    events, new_offset = read_tail(str(p), offset)

    assert events == []
    assert new_offset == offset


def test_partial_trailing_line_is_not_consumed(tmp_path):
    """A child mid-write must not cost us the line it is writing."""
    p = tmp_path / "t.jsonl"
    _write(p, _line(type="assistant", n=1) + '{"type": "assistant", "n": 2')

    events, offset = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [1]
    assert offset == len(_line(type="assistant", n=1).encode())


def test_completed_line_arrives_on_the_next_read(tmp_path):
    p = tmp_path / "t.jsonl"
    first = _line(type="assistant", n=1)
    _write(p, first + '{"type": "assistant", "n": 2')
    _, offset = read_tail(str(p), 0)

    with p.open("ab") as fh:
        fh.write(("}\n").encode())
    events, _ = read_tail(str(p), offset)

    assert [e["n"] for e in events] == [2]


def test_cold_start_windows_a_large_file_and_drops_the_partial_head(tmp_path):
    """A large file is read through a narrow tail window, not in full."""
    p = tmp_path / "t.jsonl"
    _write(p, "".join(_line(type="assistant", n=i, pad="x" * 200) for i in range(400)))
    size = p.stat().st_size
    assert size > 4096, "fixture must exceed the window for this to mean anything"

    events, offset = read_tail(str(p), 0, window=4096)

    assert events, "window must yield something"
    assert len(events) < 400, "window must not have read the whole file"
    assert offset == size
    assert all("n" in e for e in events), "a truncated head line must be dropped"
    assert events[-1]["n"] == 399, "the window is the tail, not the head"


def test_cold_start_drops_a_mid_line_cut_even_when_the_remainder_still_parses(tmp_path):
    """A mid-line cut must be dropped even if the leftover bytes are, by
    coincidence, still valid JSON on their own.

    Every line carries 3 bytes of leading whitespace before its '{' (JSON
    tolerates that). Cutting 1 byte into that whitespace is still a MID-LINE
    cut by our definition (the true line start is 2 bytes earlier, and the
    probe byte confirms it: it is a space, not the preceding newline) — but
    unlike a cut through `{"n": ...`, the remainder `  {"n": "15"}\\n...` still
    parses. A reader that forgets to drop it emits a spurious extra event for
    line 15. This is the fixture the padding-based test above cannot be:
    there, the discarded fragment is syntactically broken and gets skipped by
    json.loads regardless of whether the drop logic runs, so `lines[1:]` ->
    `lines[0:]` survives it. Here the fragment parses either way, so only the
    drop logic decides whether "15" appears.
    """
    p = tmp_path / "t.jsonl"
    lines = ["   " + _line(n="%02d" % i) for i in range(20)]
    widths = {len(l.encode()) for l in lines}
    assert len(widths) == 1, "fixture must be fixed-width to place the cut precisely"
    line_width = widths.pop()
    _write(p, "".join(lines))
    size = p.stat().st_size

    # Land 1 byte into line 15's 3-space indent (true start of line 15 is at
    # 15 * line_width; this is 1 byte past it).
    start = 15 * line_width + 1
    window = size - start

    events, offset = read_tail(str(p), 0, window=window)

    assert [e["n"] for e in events] == ["16", "17", "18", "19"], (
        "line 15's whitespace-shifted fragment must be dropped even though "
        "it still parses; keeping it would surface '15' as a spurious event"
    )
    assert offset == size


def test_cold_start_on_an_exact_line_boundary_keeps_every_line(tmp_path):
    """When the window start lands exactly on a line start, nothing is cut."""
    p = tmp_path / "t.jsonl"
    lines = [_line(n="%02d" % i) for i in range(20)]
    widths = {len(l.encode()) for l in lines}
    assert len(widths) == 1, "fixture must be fixed-width for an exact boundary to be constructible"
    line_width = widths.pop()
    _write(p, "".join(lines))

    window = 5 * line_width  # boundary falls exactly between lines 14 and 15
    events, offset = read_tail(str(p), 0, window=window)

    assert [e["n"] for e in events] == ["15", "16", "17", "18", "19"]
    assert offset == p.stat().st_size


def test_window_zero_or_negative_still_completes_the_cold_start(tmp_path):
    """A non-positive window must not stall the reader at offset 0 forever."""
    p = tmp_path / "t.jsonl"
    _write(p, _line(n=1) + _line(n=2))

    for window in (0, -1, -100000):
        events, offset = read_tail(str(p), 0, window=window)
        assert events, "window=%r must still yield events" % (window,)
        assert offset == p.stat().st_size


def test_truncated_file_resets_and_rereads(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(p, "".join(_line(type="assistant", n=i) for i in range(10)))
    _, offset = read_tail(str(p), 0)

    _write(p, _line(type="assistant", n=99))
    events, new_offset = read_tail(str(p), offset)

    assert [e["n"] for e in events] == [99]
    assert new_offset == p.stat().st_size


def test_invalid_json_line_is_skipped(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(p, _line(type="assistant", n=1) + "not json\n" + _line(type="assistant", n=2))

    events, _ = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [1, 2]


def test_non_object_json_line_is_skipped(tmp_path):
    """Valid JSON that is not a dict would break every consumer downstream."""
    p = tmp_path / "t.jsonl"
    _write(p, _line(type="assistant", n=1) + "[1, 2, 3]\n" + '"a string"\n')

    events, _ = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [1]


def test_missing_file_returns_empty(tmp_path):
    events, offset = read_tail(str(tmp_path / "nope.jsonl"), 0)

    assert events == []
    assert offset == 0


def test_empty_file_returns_empty(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(p, "")

    events, offset = read_tail(str(p), 0)

    assert events == []
    assert offset == 0


def test_directory_in_place_of_file_returns_empty(tmp_path):
    events, offset = read_tail(str(tmp_path), 0)

    assert events == []
    assert offset == 0


def test_transient_stat_failure_holds_the_bookmark_and_does_not_replay(tmp_path):
    """A stat failure must never reset a live bookmark.

    Simulates the file vanishing and returning (e.g. a rename race) by moving
    it away, confirming the read at that moment returns the bookmark
    unchanged (not 0), then moving it back and confirming the event already
    consumed is NOT re-emitted.
    """
    p = tmp_path / "t.jsonl"
    moved = tmp_path / "t.jsonl.moved"
    _write(p, _line(i=1))
    events, offset = read_tail(str(p), 0)
    assert [e["i"] for e in events] == [1]
    assert offset > 0

    os.rename(str(p), str(moved))
    during_events, during_offset = read_tail(str(p), offset)
    assert during_events == []
    assert during_offset == offset, "a transient stat failure must hold the bookmark, not reset to 0"

    os.rename(str(moved), str(p))
    after_events, after_offset = read_tail(str(p), during_offset)
    assert after_events == [], "the event already consumed before the outage must not be replayed"
    assert after_offset == offset


def test_crlf_line_endings_are_read_without_losing_events(tmp_path):
    """A transcript with CRLF endings still parses, offsets and all.

    Claude Code writes bare LF, so this is not the shape the reader meets in
    production — but CI proved the distinction matters: the fixtures here
    originally used `Path.write_text`, which silently becomes CRLF on Windows,
    and three tests failed with `assert 31 == 30` (one byte per line). The
    reader was correct both times; only the fixtures' size arithmetic was
    wrong. This pins that, so a future CRLF failure is read as a fixture bug
    and not chased into the reader.
    """
    p = tmp_path / "crlf.jsonl"
    body = "".join(_line(type="assistant", n=i) for i in range(3))
    p.write_bytes(body.replace("\n", "\r\n").encode())

    events, offset = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [0, 1, 2]
    assert offset == p.stat().st_size, "the offset is the file's, not Python's idea of it"

    with p.open("ab") as fh:
        fh.write(_line(type="assistant", n=3).replace("\n", "\r\n").encode())
    more, _ = read_tail(str(p), offset)

    assert [e["n"] for e in more] == [3]
