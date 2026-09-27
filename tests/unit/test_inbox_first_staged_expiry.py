"""A held page's expiry counts from when it first entered the inbox (#518).

It used to count from the file's mtime, so every rewrite restarted the 14
days. The extractor rewrites a staged page whenever the lesson is re-derived
(a changed ``source_hash``, or a drift/stem/similarity redirect onto the
slug), and the pages the judge calls ``generic`` are standard practice — the
ones re-derived most. They reset their own clock and never left the queue.
These pin:

- the extractor stamps ``staged_at`` on first staging and carries it over on
  every rewrite of the same staged key;
- a page staged before the stamp existed reads its mtime, and a rewrite
  writes that mtime as its stamp — no retroactive expiry at ship time;
- a rewrite whose verdict changes follows the new verdict, on the old clock;
- the sweep and the ``--json`` listing read the same time.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from mnemo.core import inbox as I
from mnemo.core import llm as llm_mod
from mnemo.core.extract import reference_gate as rg
from mnemo.core.extract import run_extraction
from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.extract.scanner import parse_frontmatter

DAY = 86400


def _held(vault: Path, slug: str, *, mtime_age_days: float, stamp: str = "") -> Path:
    path = vault / "shared" / "_inbox" / "reference" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp_line = f"{I.STAGED_AT}: {stamp}\n" if stamp else ""
    path.write_text(
        f"---\nname: {slug}\nslug: {slug}\ndescription: d\ntype: reference\n"
        f"reference_gate: generic\n{stamp_line}sources:\n  - bots/demo/x.md\n---\n\nbody\n",
        encoding="utf-8",
    )
    ts = time.time() - mtime_age_days * DAY - 60
    os.utime(path, (ts, ts))
    return path


def _days_ago(days: float) -> str:
    return (datetime.now() - timedelta(days=days, minutes=1)).isoformat(timespec="seconds")


# --- the stamp --------------------------------------------------------------


def _extracted() -> ExtractedPage:
    return ExtractedPage(
        slug="p", type="reference", name="Name", description="d", body="Body.",
        source_files=["bots/a/memory/x.md"], source_hash="h", judged="G",
    )


def test_a_staged_page_carries_the_time_it_first_entered_the_inbox():
    fm, _ = parse_frontmatter(_render_page(_extracted(), run_id="r", staged_at="2026-09-01T10:00:00"))

    assert fm[I.STAGED_AT] == "2026-09-01T10:00:00"


def test_a_live_page_carries_no_staging_time():
    text = _render_page(_extracted(), run_id="r", auto_promoted=True, staged_at="2026-09-01T10:00:00")

    assert I.STAGED_AT not in parse_frontmatter(text)[0]


def test_a_page_not_on_disk_is_staged_now(tmp_path: Path):
    now = datetime(2026, 9, 27, 12, 0, 0)

    assert I.staged_at_for(tmp_path / "absent.md", now=now) == "2026-09-27T12:00:00"


def test_a_rewrite_keeps_the_stamp_already_there(tmp_vault: Path):
    stamp = _days_ago(10)
    path = _held(tmp_vault, "p", mtime_age_days=1, stamp=stamp)

    assert I.staged_at_for(path) == stamp


def test_a_page_staged_before_the_stamp_existed_keeps_its_mtime(tmp_vault: Path):
    """The migration: an unstamped page's first-staging time is its current
    mtime, and the rewrite that stamps it writes exactly that."""
    path = _held(tmp_vault, "p", mtime_age_days=5)
    mtime = path.stat().st_mtime

    assert I.staged_at_for(path) == datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
    assert I.staged_pages(tmp_vault)[0].staged_since == mtime


def test_a_stamp_later_than_the_file_reads_as_the_file(tmp_vault: Path):
    """A hand-edited future stamp cannot postpone an expiry."""
    path = _held(tmp_vault, "p", mtime_age_days=20, stamp="2099-01-01T00:00:00")

    assert I.staged_pages(tmp_vault)[0].staged_since == path.stat().st_mtime
    assert [r.ok for r in I.expire_held(tmp_vault, days=14)] == [True]


def test_a_garbled_stamp_reads_as_the_mtime(tmp_vault: Path):
    path = _held(tmp_vault, "p", mtime_age_days=3, stamp="not-a-date")

    assert I.staged_pages(tmp_vault)[0].staged_since == path.stat().st_mtime


# --- the sweep --------------------------------------------------------------


def test_a_page_rewritten_yesterday_but_staged_long_ago_expires(tmp_vault: Path):
    """The bug: mtime 1 day, first staged 15 days ago — due, not 13 days away."""
    _held(tmp_vault, "recurring", mtime_age_days=1, stamp=_days_ago(15))

    results = I.expire_held(tmp_vault, days=14)

    assert [r.ok for r in results] == [True]
    assert not (tmp_vault / "shared" / "_inbox" / "reference" / "recurring.md").exists()


def test_a_page_staged_recently_is_not_expired_by_an_old_mtime(tmp_vault: Path):
    """The stamp is capped at the mtime, never pushed back by it: a page
    staged 3 days ago with an mtime of 3 days is 3 days old."""
    _held(tmp_vault, "fresh", mtime_age_days=3, stamp=_days_ago(3))

    assert I.expire_held(tmp_vault, days=14) == []


def test_the_listing_and_the_sweep_read_the_same_time(tmp_vault: Path):
    _held(tmp_vault, "recurring", mtime_age_days=1, stamp=_days_ago(10))
    page = I.staged_pages(tmp_vault)[0]

    row = I.page_record(page, cfg={}, restored=frozenset())

    staged = datetime.fromisoformat(row["staged_at"])
    assert abs(staged.timestamp() - page.staged_since) < 1
    assert datetime.fromisoformat(row["expires_at"]) == I.expires_at(page, {})
    assert I.expires_at(page, {}) == datetime.fromtimestamp(page.staged_since) + timedelta(days=14)


# --- end to end: a re-derived page through the extractor ---------------------


def _extraction_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "shared").mkdir(parents=True)
    d = root / "bots" / "agent" / "memory"
    d.mkdir(parents=True)
    (d / "one.md").write_text(
        "---\nname: one\ntype: reference\ndescription: d\n---\n\nbody one\n", encoding="utf-8",
    )
    return root


def _cfg(root: Path) -> dict:
    return {
        "vaultRoot": str(root),
        "extraction": {"model": "m", "chunkSize": 10, "subprocessTimeout": 60,
                       "referenceGate": {"enabled": True, "model": "judge-model"}},
        "inbox": {},
    }


def _stub_llm(monkeypatch, verdict: str, body: str) -> None:
    pages = [{"slug": "dependency-injection", "type": "reference", "name": "DI is good",
              "description": "d", "body": body,
              "source_files": ["bots/agent/memory/one.md"]}]

    def call(prompt, *, system, model, timeout):
        payload = ({"verdicts": [{"i": 1, "cat": verdict}]} if system == rg.SYSTEM_PROMPT
                   else {"pages": pages})
        return llm_mod.LLMResponse(text=json.dumps(payload), total_cost_usd=0.0,
                                   input_tokens=1, output_tokens=1,
                                   api_key_source="none", raw={})

    monkeypatch.setattr(llm_mod, "call", call)


def _rederive(root: Path, monkeypatch, verdict: str, n: int) -> None:
    """The source says something new, so the page re-derives with a new hash."""
    (root / "bots" / "agent" / "memory" / "one.md").write_text(
        f"---\nname: one\ntype: reference\ndescription: d\n---\n\nbody {n}\n", encoding="utf-8",
    )
    _stub_llm(monkeypatch, verdict, f"Inject dependencies, take {n}.")
    run_extraction(_cfg(root))


def test_a_re_derived_page_keeps_its_first_staging_through_verdict_changes(tmp_path, monkeypatch):
    root = _extraction_vault(tmp_path)
    staged = root / "shared" / "_inbox" / "reference" / "dependency-injection.md"
    _stub_llm(monkeypatch, "G", "Inject dependencies.")
    run_extraction(_cfg(root))
    first = parse_frontmatter(staged.read_text(encoding="utf-8"))[0][I.STAGED_AT]

    # Pretend that run was ten days ago: the stamp is capped at the mtime, so
    # the page now reads as staged ten days back, and the next rewrite writes
    # that as its stamp.
    ten_days_ago = time.time() - 10 * DAY
    os.utime(staged, (ten_days_ago, ten_days_ago))
    since = I.staged_pages(root)[0].staged_since
    assert first  # stamped on first staging
    assert abs(since - ten_days_ago) < 1

    # Re-derived, still generic: rewritten, clock untouched.
    _rederive(root, monkeypatch, "G", 2)
    page = I.staged_pages(root)[0]
    assert "take 2" in staged.read_text(encoding="utf-8")
    assert page.mtime > since + 9 * DAY
    assert abs(page.staged_since - since) < 1
    assert I.expires_at(page, {}) == datetime.fromtimestamp(page.staged_since) + timedelta(days=14)

    # The judge now keeps it: it stops expiring.
    _rederive(root, monkeypatch, "S", 3)
    page = I.staged_pages(root)[0]
    assert page.gate_verdict == "system"
    assert I.expires_at(page, {}) is None
    assert abs(page.staged_since - since) < 1

    # Let go again: its window is counted from the first staging, not this run.
    _rederive(root, monkeypatch, "G", 4)
    page = I.staged_pages(root)[0]
    assert page.gate_held
    assert abs(page.staged_since - since) < 1

    # Four more days and it is due, however recently it was rewritten.
    later = datetime.fromtimestamp(since) + timedelta(days=14, minutes=1)
    assert [r.why for r in I.expire_held(root, days=14, now=later)] == [I.WHY_HELD]
