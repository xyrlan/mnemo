"""``tools/measure_held_expiry.py`` over a synthetic vault (#518)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_held_expiry as tool  # noqa: E402

NOW = datetime(2026, 9, 27, 12, 0, 0)


def _page(vault: Path, slug: str, *, mtime: datetime, gate: str = "generic", stamp: str = "") -> None:
    path = vault / "shared" / "_inbox" / "reference" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = f"staged_at: {stamp}\n" if stamp else ""
    path.write_text(
        f"---\nname: {slug}\ntype: reference\nreference_gate: {gate}\n{extra}"
        f"sources:\n  - bots/demo/x.md\n---\n\nbody\n",
        encoding="utf-8",
    )
    os.utime(path, (mtime.timestamp(), mtime.timestamp()))


def _ledger(vault: Path, *rows: tuple) -> None:
    path = vault / ".mnemo" / "inbox-offers.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(
        json.dumps({"ts": ts.isoformat(timespec="seconds"), "event": ev, "key": key}) + "\n"
        for ts, ev, key in rows
    ), encoding="utf-8")


def test_first_offered_restarts_after_a_key_leaves_the_queue():
    t = [NOW - timedelta(days=d) for d in (9, 8, 3)]
    rows = [
        {"ts": t[0].isoformat(), "event": "offered", "key": "reference/a"},
        {"ts": t[1].isoformat(), "event": "dropped", "key": "reference/a"},
        {"ts": t[2].isoformat(), "event": "offered", "key": "reference/a"},
        {"ts": t[2].isoformat(), "event": "offered", "key": "reference/b"},
        {"ts": NOW.isoformat(), "event": "offered", "key": "reference/b"},
    ]

    assert tool.first_offered(rows) == {"reference/a": t[2], "reference/b": t[2]}


def test_a_page_offered_before_its_mtime_counts_as_rewritten(tmp_path: Path):
    _page(tmp_path, "rewritten", mtime=NOW - timedelta(days=1))
    _page(tmp_path, "same-write", mtime=NOW - timedelta(days=5))
    _page(tmp_path, "never-offered", mtime=NOW - timedelta(days=2))
    _page(tmp_path, "kept", mtime=NOW - timedelta(days=30), gate="system")
    _ledger(tmp_path,
            (NOW - timedelta(days=10), "offered", "reference/rewritten"),
            (NOW - timedelta(days=5), "offered", "reference/same-write"))

    report = tool.measure(tmp_path, now=NOW)

    assert report["staged"] == 4 and report["expiring"] == 3
    assert report["rewritten_keys"] == ["reference/rewritten"]
    assert report["moved_by_ledger"] == 1
    # The ledger alternative would put the rewritten page's expiry at
    # offer + 14 = 4 days from now; the shipped rule reads its mtime.
    assert report["by_date"]["ledger"]["2026-10-01"] == 1
    assert report["by_date"]["after"] == report["by_date"]["before"]
    assert report["due_now"] == {"before": 0, "after": 0, "ledger": 0}


def test_after_reads_the_stamp_where_before_reads_the_mtime(tmp_path: Path):
    stamp = (NOW - timedelta(days=20)).isoformat(timespec="seconds")
    _page(tmp_path, "stamped", mtime=NOW - timedelta(days=1), stamp=stamp)

    report = tool.measure(tmp_path, now=NOW)

    assert report["due_now"] == {"before": 0, "after": 1, "ledger": 1}
    assert "2026-10-10" in report["by_date"]["before"]


def test_render_and_json_run(tmp_path: Path, capsys):
    _page(tmp_path, "a", mtime=NOW - timedelta(days=1))

    assert tool.main(["--vault", str(tmp_path)]) == 0
    assert "expiring 1" in capsys.readouterr().out
    assert tool.main(["--vault", str(tmp_path), "--json", "--days", "7"]) == 0
    assert json.loads(capsys.readouterr().out)["days"] == 7
