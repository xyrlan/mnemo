"""``tools/measure_account_roots.py`` over synthetic config dirs and a ledger (#640)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_account_roots as tool  # noqa: E402

from mnemo.core.sessions.accounts import Root  # noqa: E402


def _root(home: Path, *ids: str, workers=None) -> Root:
    for short_id in ids:
        (home / "jobs" / short_id).mkdir(parents=True)
        (home / "jobs" / short_id / "state.json").write_text("{}", encoding="utf-8")
    if workers is not None:
        (home / "daemon").mkdir(parents=True)
        (home / "daemon" / "roster.json").write_text(
            json.dumps({"workers": {w: {"pid": 1} for w in workers}}), encoding="utf-8")
    return Root(home=home, jobs=home / "jobs")


def test_each_root_is_counted_and_a_shared_id_is_counted_once(tmp_path: Path) -> None:
    default = _root(tmp_path / ".claude", "aaaa1111", "bbbb2222", workers=[])
    account = _root(tmp_path / ".claude-acct", "bbbb2222", "cccc3333", "dddd4444",
                    workers=["cccc3333"])
    (account.jobs / "pins.json").write_text("{}", encoding="utf-8")  # not a job
    (account.jobs / "empty").mkdir()                                 # no state.json
    missing = Root(home=tmp_path / "gone", jobs=tmp_path / "gone" / "jobs")

    report = tool.count_roots([account, default, missing], default.jobs)

    assert report["roots"] == [
        {"root": "account 1", "jobs": 3, "roster_workers": 1},
        {"root": "default", "jobs": 2, "roster_workers": 0},
        {"root": "account 2", "jobs": 0, "roster_workers": None},
    ]
    assert report["distinct_ids"] == 4
    assert report["ids_under_two_roots"] == 1
    assert report["where"]["bbbb2222"] == ["account 1", "default"]


def test_session_gone_follows_are_split_by_where_the_job_is_now() -> None:
    since = 1_000.0
    ledger = {"children": {
        "cccc3333": {"closed": "session-gone", "closed_at": 2_000.0},
        "aaaa1111": {"closed": "session-gone", "closed_at": 2_000.0},
        "eeee5555": {"closed": "session-gone", "closed_at": 2_000.0},
        "bbbb2222": {"closed": "session-gone", "closed_at": 1_000.0},  # in both: non-default
        "old00000": {"closed": "session-gone", "closed_at": 999.0},    # before the window
        "merged00": {"closed": "merged", "closed_at": 2_000.0},
        "open0000": {"closed": None, "closed_at": None},
        "junk": "not an entry",
    }}
    where = {"cccc3333": ["account 1"], "aaaa1111": ["default"],
             "bbbb2222": ["account 1", "default"]}

    assert tool.session_gone(ledger, since, where) == {
        "total": 4, "job_under_non_default": 2, "job_under_default": 1, "job_nowhere": 1,
    }
    assert tool.session_gone({}, since, where)["total"] == 0
    assert tool.session_gone([], since, where)["total"] == 0


def test_the_report_never_spells_a_path(tmp_path: Path) -> None:
    account = _root(tmp_path / ".claude-someone-example-com", "cccc3333")
    default = _root(tmp_path / ".claude", "aaaa1111")
    report = tool.count_roots([account, default], default.jobs)
    report["since"] = "2026-10-07"
    report["session_gone"] = tool.session_gone({}, 0.0, report.pop("where"))

    text = tool.render(report)

    assert "someone" not in text and str(tmp_path) not in text
    assert "account 1" in text and "default" in text
    assert "session-gone since 2026-10-07: 0" in text
