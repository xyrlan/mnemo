"""A review queue with no reviewer (#762): demotions expire, the offer backs off.

On a vault on 2026-10-27, 344 of 512 staged pages were evidence-gate
demotions, and two weeks of daily offers had produced 0 promotions and 0
drops. These pin the two exits that do not wait for a human:

- a demotion the judge did not hold expires on ``inbox.demotionExpiryDays``,
  undoably, and nothing else that never expired before starts to;
- while nothing is decided for ``inbox.offerIdleDays``, the vault gets one
  block per ``inbox.offerIdleIntervalHours`` instead of one per project per
  day, and the first decision restores the daily pace.

``tools/measure_inbox_backlog.py`` is pinned at the bottom, over a synthetic
inbox shaped like the one the issue found.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from mnemo.core import inbox as I
from mnemo.core.config import DEFAULTS

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_inbox_backlog as tool  # noqa: E402

NOW = datetime(2026, 10, 28, 12, 0, 0)


def _cfg(**over) -> dict:
    return {**DEFAULTS, "inbox": {**DEFAULTS["inbox"], **over}}


def _page(vault: Path, slug: str, *, age_days: float, extra: str = "",
          project: str = "demo") -> Path:
    path = vault / "shared" / "_inbox" / "reference" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {slug}\nslug: {slug}\ndescription: d\ntype: reference\n{extra}"
        f"sources:\n  - bots/{project}/x.md\n---\n\nbody\n",
        encoding="utf-8",
    )
    ts = (NOW - timedelta(days=age_days, minutes=1)).timestamp()
    os.utime(path, (ts, ts))
    return path


DEMOTED = "demoted_from: feedback\n"
MULTI = ""  # multi-source is decided by `sources`, built below


def _multi(vault: Path, slug: str, *, age_days: float) -> Path:
    path = vault / "shared" / "_inbox" / "reference" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {slug}\ntype: reference\n"
        "sources:\n  - bots/demo/x.md\n  - bots/demo/y.md\n---\n\nbody\n",
        encoding="utf-8",
    )
    ts = (NOW - timedelta(days=age_days, minutes=1)).timestamp()
    os.utime(path, (ts, ts))
    return path


def _ledger(vault: Path, *rows: tuple) -> None:
    path = vault / I.LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(
        json.dumps({"ts": ts.isoformat(timespec="seconds"), "event": ev,
                    "key": key, "project": project}) + "\n"
        for ts, ev, key, project in rows
    ), encoding="utf-8")


def _keys(results) -> list:
    return sorted(r.moved_to.stem for r in results if r.ok)


# --- demotions expire --------------------------------------------------------


def test_a_demotion_past_its_window_is_archived_and_a_young_one_is_not(tmp_path):
    _page(tmp_path, "old-keep", age_days=15, extra=DEMOTED + "reference_gate: system\n")
    _page(tmp_path, "old-unjudged", age_days=15, extra=DEMOTED)
    _page(tmp_path, "young", age_days=3, extra=DEMOTED)

    out = I.expire_held(tmp_path, days=14, demotion_days=14, now=NOW)

    assert _keys(out) == ["old-keep", "old-unjudged"]
    assert {r.why for r in out} == {I.WHY_DEMOTION}
    assert [p.slug for p in I.staged_pages(tmp_path)] == ["young"]
    assert all(r["event"] == I.EXPIRED for r in I.read_ledger(tmp_path))


def test_what_never_expired_before_still_does_not(tmp_path):
    _multi(tmp_path, "multi", age_days=90)
    _page(tmp_path, "other", age_days=90)
    _page(tmp_path, "kept-reference", age_days=90, extra="reference_gate: technique\n")

    assert I.expire_held(tmp_path, days=14, demotion_days=14, now=NOW) == []
    assert len(I.staged_pages(tmp_path)) == 3


def test_a_demotion_the_judge_held_keeps_the_held_window(tmp_path):
    """First match wins: a G/N demotion was already expiring before #762."""
    _page(tmp_path, "generic", age_days=10, extra=DEMOTED + "reference_gate: generic\n")
    (page,) = I.staged_pages(tmp_path)
    assert page.expiry_why == I.WHY_HELD

    cfg = _cfg(heldExpiryDays=7, demotionExpiryDays=30)
    assert I.expires_at(page, cfg) == datetime.fromtimestamp(page.staged_since) + timedelta(days=7)


def test_zero_turns_demotion_expiry_off_and_a_caller_that_predates_it_gets_zero(tmp_path):
    _page(tmp_path, "old", age_days=40, extra=DEMOTED)

    assert I.expire_held(tmp_path, days=14, now=NOW) == []
    assert I.expires_at(I.staged_pages(tmp_path)[0], _cfg(demotionExpiryDays=0)) is None
    assert I.demotion_expiry_days(_cfg(demotionExpiryDays="x")) == I.DEMOTION_EXPIRY_DAYS


def test_an_expired_demotion_comes_back_with_restore_and_never_expires_again(tmp_path):
    _page(tmp_path, "back", age_days=20, extra=DEMOTED)
    assert _keys(I.expire_held(tmp_path, days=14, demotion_days=14, now=NOW)) == ["back"]

    assert I.restore(tmp_path, "reference/back").ok
    (page,) = I.staged_pages(tmp_path)
    assert I.expires_at(page, _cfg(), restored=I.restored_keys(tmp_path)) is None


# --- the offer backs off ----------------------------------------------------


def test_an_ignored_queue_gets_one_block_a_week_for_the_whole_vault(tmp_path):
    _page(tmp_path, "a", age_days=2, project="one")
    _page(tmp_path, "b", age_days=2, project="two")
    _ledger(tmp_path,
            (NOW - timedelta(days=10), "offered", "reference/old", "one"),
            (NOW - timedelta(days=2), "offered", "reference/older", "two"))

    # Project "one" is a day past its own interval, but a block went out to
    # "two" two days ago and nobody has decided anything in ten.
    assert I.pick_offers(tmp_path, "one", cfg=_cfg(), now=NOW) == ([], 0)
    assert I.offer_idle(tmp_path, cfg=_cfg(), now=NOW)

    later = NOW + timedelta(days=6)
    pages, _ = I.pick_offers(tmp_path, "one", cfg=_cfg(), now=later)
    assert [p.slug for p in pages] == ["a"]


def test_a_decision_restores_the_daily_pace(tmp_path):
    _page(tmp_path, "a", age_days=2, project="one")
    _ledger(tmp_path,
            (NOW - timedelta(days=10), "offered", "reference/old", "one"),
            (NOW - timedelta(days=3), "dropped", "reference/old", "one"),
            (NOW - timedelta(days=2), "offered", "reference/x", "two"))

    assert not I.offer_idle(tmp_path, cfg=_cfg(), now=NOW)
    assert [p.slug for p in I.pick_offers(tmp_path, "one", cfg=_cfg(), now=NOW)[0]] == ["a"]


def test_a_vault_offered_for_less_than_the_idle_window_is_not_idle(tmp_path):
    """Without this half, the first day of offers would already read as ignored."""
    _ledger(tmp_path, (NOW - timedelta(days=3), "offered", "reference/x", "one"))

    assert not I.offer_idle(tmp_path, cfg=_cfg(), now=NOW)
    assert not I.offer_idle(tmp_path, cfg=_cfg(), now=NOW + timedelta(days=3))
    assert I.offer_idle(tmp_path, cfg=_cfg(), now=NOW + timedelta(days=5))


def test_zero_idle_days_turns_the_backoff_off(tmp_path):
    _page(tmp_path, "a", age_days=2, project="one")
    _ledger(tmp_path,
            (NOW - timedelta(days=30), "offered", "reference/old", "one"),
            (NOW - timedelta(hours=30), "offered", "reference/x", "two"))

    pages, _ = I.pick_offers(tmp_path, "one", cfg=_cfg(offerIdleDays=0), now=NOW)
    assert [p.slug for p in pages] == ["a"]


# --- the dry run --------------------------------------------------------------


def _inbox_like_the_issues(vault: Path) -> None:
    """Demotions old and young, judged and not; multi-source and other pages."""
    for i in range(4):
        _page(vault, f"dem-sys-{i}", age_days=20, extra=DEMOTED + "reference_gate: system\n")
    _page(vault, "dem-tech", age_days=16, extra=DEMOTED + "reference_gate: technique\n")
    _page(vault, "dem-unjudged", age_days=15, extra=DEMOTED)
    _page(vault, "dem-young", age_days=5, extra=DEMOTED + "reference_gate: system\n")
    _page(vault, "dem-generic", age_days=20, extra=DEMOTED + "reference_gate: generic\n")
    _multi(vault, "multi", age_days=30)
    _page(vault, "other", age_days=30)


def test_the_dry_run_counts_what_each_rule_would_archive_by_kind(tmp_path):
    _inbox_like_the_issues(tmp_path)

    report = tool.measure(tmp_path, _cfg(), now=NOW)

    e = report["expiry"]
    assert e["staged"] == 10
    assert e["by_reason"] == {"demotion": 8, "multi-source": 1, "other": 1}
    assert e["due_old"] == {"demotion": 1}          # the judge-held one
    assert e["due_new"] == {"demotion": 7}
    assert e["newly_due_by_verdict"] == {"system": 4, "technique": 1, "unjudged": 1}
    # Read only: nothing moved, nothing logged.
    assert len(I.staged_pages(tmp_path)) == 10
    assert I.read_ledger(tmp_path) == []


def test_the_replay_keeps_blocks_until_the_queue_is_idle_then_one_a_week(tmp_path):
    start = NOW - timedelta(days=21)
    rows = []
    for day in range(21):
        for n, project in enumerate(("one", "two")):
            ts = start + timedelta(days=day, hours=n)
            rows += [(ts, "offered", f"reference/{project}-{day}-{k}", project) for k in (0, 1)]
    _ledger(tmp_path, *rows)

    o = tool.measure(tmp_path, _cfg(), now=NOW)["offer"]

    assert o["blocks"] == 42 and o["human_decisions"] == 0
    # Days 0-6 are not yet idle: all 14 blocks. From day 7 the vault is, and
    # gets one block per 168 h after the last one kept (day 6, "two"): the
    # ones on day 13 and day 20, "two" each — "one" is an hour short.
    assert o["kept_under_backoff"] == 16


def test_the_session_scan_counts_the_hooks_block_not_a_quote_of_it(tmp_path):
    projects = tmp_path / "projects"
    child = projects / "child"
    child.mkdir(parents=True)
    mark = tool.BLOCK_MARK + "demo, 3 waiting]"
    (child / "a.jsonl").write_text(json.dumps(
        {"type": "attachment", "cwd": str(tmp_path / "demo-wt-12"), "content": mark}
    ) + "\n", encoding="utf-8")
    (projects / "top.jsonl").write_text(json.dumps(
        {"type": "attachment", "cwd": str(tmp_path / "demo"), "content": mark}
    ) + "\n", encoding="utf-8")
    (projects / "quote.jsonl").write_text(json.dumps(
        {"type": "user", "cwd": str(tmp_path / "demo"), "content": mark}
    ) + "\n", encoding="utf-8")

    assert tool.sessions(projects) == {"interactive": 1, "dispatch": 1}
