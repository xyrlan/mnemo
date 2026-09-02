"""The learned ledger — what extraction promoted, and what a project still owes an announcement.

Ordering is the load-bearing property: entries recorded in one ``record`` call
share a timestamp, so ``pending`` compares against the per-project marker on a
monotonic integer ``seq`` rather than on ``ts``.
"""
import json
from pathlib import Path

from mnemo.core import learned


def _e(slug, projects, confidence="inferred", quote=None, type_="feedback"):
    return {"slug": slug, "type": type_, "name": slug.title(), "projects": projects,
            "confidence": confidence, "quote": quote}


def test_record_and_pending_roundtrip(tmp_path: Path):
    learned.record(tmp_path, run_id="r1", entries=[
        _e("use-yarn", ["proj"], "verified", "use yarn not npm"),
        _e("other", ["zzz"]),
    ])
    assert [e["slug"] for e in learned.pending(tmp_path, "proj")] == ["use-yarn"]
    learned.mark_announced(tmp_path, "proj")
    assert learned.pending(tmp_path, "proj") == []
    learned.record(tmp_path, run_id="r2", entries=[_e("n2", ["proj"])])
    assert [e["slug"] for e in learned.pending(tmp_path, "proj")] == ["n2"]


def test_universal_entries_pend_for_every_project(tmp_path: Path):
    learned.record(tmp_path, run_id="r1", entries=[_e("u", ["a", "b"], "verified", "q")])
    assert [e["slug"] for e in learned.pending(tmp_path, "zzz")] == ["u"]


def test_marker_is_per_project(tmp_path: Path):
    learned.record(tmp_path, run_id="r1", entries=[_e("x", ["a", "b"])])
    learned.mark_announced(tmp_path, "a")
    assert learned.pending(tmp_path, "a") == []
    assert [e["slug"] for e in learned.pending(tmp_path, "b")] == ["x"]


def test_corrupt_lines_are_skipped(tmp_path: Path):
    (tmp_path / ".mnemo").mkdir()
    (tmp_path / ".mnemo" / "learned.jsonl").write_text(
        "not json\n{\"slug\": \"ok\", \"projects\": [\"p\"]}\n", encoding="utf-8")
    assert [e["slug"] for e in learned.pending(tmp_path, "p")] == ["ok"]


def test_limit_and_order(tmp_path: Path):
    learned.record(tmp_path, run_id="r1", entries=[_e(f"s{i}", ["p"]) for i in range(7)])
    got = learned.pending(tmp_path, "p", limit=5)
    assert [e["slug"] for e in got] == ["s0", "s1", "s2", "s3", "s4"]
    assert learned.pending_count(tmp_path, "p") == 7


def test_rotation_keeps_the_tail(tmp_path: Path):
    big = [_e(f"s{i}", ["p"], quote="x" * 200) for i in range(6000)]
    learned.record(tmp_path, run_id="r1", entries=big)
    assert (tmp_path / ".mnemo" / "learned.jsonl").stat().st_size < 2 * 1024 * 1024
    assert learned.pending(tmp_path, "p", limit=1)[0]["slug"].startswith("s")


def test_pending_never_raises_on_a_broken_vault(tmp_path: Path):
    """The reader runs inside a SessionStart hook — I/O trouble must be inert."""
    ledger = tmp_path / ".mnemo" / "learned.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.mkdir()  # a directory where the ledger should be
    assert learned.pending(tmp_path, "p") == []
    assert learned.pending_count(tmp_path, "p") == 0
    assert learned.mark_announced(tmp_path, "p") is None


def test_marker_survives_later_records(tmp_path: Path):
    learned.record(tmp_path, run_id="r1", entries=[_e("a", ["p"]), _e("b", ["p"])])
    learned.mark_announced(tmp_path, "p")
    learned.record(tmp_path, run_id="r2", entries=[_e("c", ["p"])])
    learned.mark_announced(tmp_path, "p")
    learned.record(tmp_path, run_id="r3", entries=[_e("d", ["p"])])
    assert [e["slug"] for e in learned.pending(tmp_path, "p")] == ["d"]


# --- surviving a ledger that was damaged between runs -----------------------

def test_a_torn_last_line_does_not_swallow_the_next_entry(tmp_path: Path):
    """A crashed append leaves a fragment with no newline; the next one must not glue to it."""
    ledger = tmp_path / ".mnemo" / "learned.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps({"seq": 1, "slug": "done", "projects": ["p"]}) + "\n"
        + '{"seq": 2, "slug": "tor',  # torn mid-write, no trailing newline
        encoding="utf-8",
    )

    learned.record(tmp_path, run_id="r2", entries=[_e("after-the-tear", ["p"])])

    assert [e["slug"] for e in learned.pending(tmp_path, "p")] == ["done", "after-the-tear"]


def test_a_marker_above_every_live_seq_does_not_block_new_entries(tmp_path: Path):
    """learned.jsonl deleted by hand, announced.json survived — seqs must clear the marker."""
    (tmp_path / ".mnemo").mkdir()
    (tmp_path / ".mnemo" / "announced.json").write_text(
        json.dumps({"p": 999}), encoding="utf-8")

    learned.record(tmp_path, run_id="r1", entries=[_e("fresh", ["p"])])

    assert [e["slug"] for e in learned.pending(tmp_path, "p")] == ["fresh"]


def test_a_hand_written_line_gets_the_seq_after_its_neighbour(tmp_path: Path):
    """Not the line index: a rotated tail starts at a seq far above its line count."""
    ledger = tmp_path / ".mnemo" / "learned.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps({"seq": 500, "slug": "rotated", "projects": ["p"]}) + "\n"
        + json.dumps({"slug": "handwritten", "projects": ["p"]}) + "\n",
        encoding="utf-8",
    )

    got = learned.pending(tmp_path, "p")

    assert [(e["slug"], e["seq"]) for e in got] == [("rotated", 500), ("handwritten", 501)]


# --- the universal threshold is the caller's, not a constant ---------------

def test_universal_threshold_is_a_parameter(tmp_path: Path):
    """scoping.universalThreshold=3 means a two-project rule is still local."""
    learned.record(tmp_path, run_id="r1", entries=[_e("two", ["a", "b"])])

    assert learned.pending(tmp_path, "zzz", universal_threshold=3) == []
    assert learned.pending_count(tmp_path, "zzz", universal_threshold=3) == 0
    assert [e["slug"] for e in learned.pending(tmp_path, "zzz")] == ["two"]


# --- `mnemo status` view: the tail, announced or not ------------------------

def test_recent_is_newest_first_and_ignores_the_marker(tmp_path: Path):
    """status lists what was already announced — that is the point of looking."""
    learned.record(tmp_path, run_id="r1", entries=[_e("a", ["p"]), _e("b", ["p"])])
    learned.mark_announced(tmp_path, "p")
    learned.record(tmp_path, run_id="r2", entries=[_e("c", ["p"])])

    assert [e["slug"] for e in learned.pending(tmp_path, "p")] == ["c"]  # marker is live
    assert [e["slug"] for e in learned.recent(tmp_path, "p")] == ["c", "b", "a"]


def test_recent_respects_limit_and_the_project_filter(tmp_path: Path):
    learned.record(tmp_path, run_id="r1", entries=[_e(f"s{i}", ["p"]) for i in range(12)])
    learned.record(tmp_path, run_id="r2", entries=[_e("elsewhere", ["zzz"])])

    got = learned.recent(tmp_path, "p", limit=3)

    assert [e["slug"] for e in got] == ["s11", "s10", "s9"]
    assert "elsewhere" not in [e["slug"] for e in learned.recent(tmp_path, "p", limit=50)]


def test_recent_with_no_project_is_vault_wide(tmp_path: Path):
    learned.record(tmp_path, run_id="r1", entries=[_e("mine", ["p"]), _e("theirs", ["zzz"])])

    assert [e["slug"] for e in learned.recent(tmp_path, None)] == ["theirs", "mine"]


def test_recent_honours_the_universal_threshold(tmp_path: Path):
    learned.record(tmp_path, run_id="r1", entries=[_e("two", ["a", "b"])])

    assert [e["slug"] for e in learned.recent(tmp_path, "zzz")] == ["two"]
    assert learned.recent(tmp_path, "zzz", universal_threshold=3) == []


def test_recent_never_raises_on_a_broken_vault(tmp_path: Path):
    ledger = tmp_path / ".mnemo" / "learned.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.mkdir()
    assert learned.recent(tmp_path, "p") == []
