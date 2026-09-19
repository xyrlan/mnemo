"""The ``shared/_inbox/`` review queue and the two acts that clear it (#380).

Extraction fills that directory by itself and, until this, nothing drained it
but a hand ``mv``: 194 plain staged pages on the real vault on 2026-09-19,
median age 5.3 days, oldest 16.2 — every one of them invisible to recall while
it waited.

What these pin, in order: what counts as staged and whose it is, that promote
moves the page *and* the extractor's ledger with it, that drop archives before
it deletes, and that the numbers a maintainer would quote come out of the
ledger rather than out of a guess.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from mnemo.core import inbox as I


def _page(
    vault: Path,
    rel: str,
    *,
    sources: list[str] | None = None,
    extra: str = "",
    age_days: float = 0.0,
    description: str = "what it says",
) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    src = "\n".join(f"  - {s}" for s in (sources or []))
    path.write_text(
        f"---\nname: {path.stem}\nslug: {path.stem}\n"
        f"description: {description}\ntype: {path.parent.name}\n{extra}"
        f"sources:\n{src}\n---\n\nbody\n",
        encoding="utf-8",
    )
    if age_days:
        # A minute past the day boundary: ages floor, and Windows' coarse clock can
        # read `now` a tick before the `time.time()` this was computed from.
        ts = time.time() - age_days * 86400 - 60
        os.utime(path, (ts, ts))
    return path


def _state(vault: Path, key: str, **over) -> None:
    entry = {
        "source_files": ["bots/demo/x.md"],
        "source_hash": "sh",
        "written_hash": "wh",
        "written_at": "r",
        "last_sync": "r",
        "status": "inbox",
        "origin_backfill": False,
        "unverified_feedback": False,
    }
    entry.update(over)
    path = vault / ".mnemo" / "extraction-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 2, "last_run": "r", "entries": {key: entry}}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# --- what is in the queue --------------------------------------------------


def test_staged_pages_are_oldest_first_with_their_reason(tmp_vault: Path):
    """Oldest first, because age is what this queue is judged on."""
    _page(tmp_vault, "shared/_inbox/reference/demo__new.md",
          sources=["bots/demo/a.md", "bots/other/b.md"], age_days=1)
    _page(tmp_vault, "shared/_inbox/reference/demo__old.md",
          sources=["bots/demo/a.md"], extra="demoted_from: feedback\n", age_days=12)

    pages = I.staged_pages(tmp_vault)

    assert [p.key for p in pages] == ["reference/demo__old", "reference/demo__new"]
    assert [p.reason for p in pages] == ["demotion", "multi-source"]
    assert pages[0].age_days() == 12


def test_a_staged_rewrite_belongs_to_mnemo_rewrites_not_here(tmp_vault: Path):
    """``.proposed.md`` is a rewrite of a live rule; a different command owns it."""
    _page(tmp_vault, "shared/_inbox/reference/demo__x.proposed.md", sources=["bots/demo/a.md"])
    _page(tmp_vault, "shared/_inbox/reference/demo__y.md", sources=["bots/demo/a.md"])

    assert [p.key for p in I.staged_pages(tmp_vault)] == ["reference/demo__y"]


def test_project_scoping_matches_the_activation_index(tmp_vault: Path):
    """``bots/<name>/`` in a source path, with the frontmatter key as fallback."""
    _page(tmp_vault, "shared/_inbox/reference/a.md", sources=["bots/demo/x.md"])
    _page(tmp_vault, "shared/_inbox/reference/b.md", sources=["bots/other/x.md"])
    _page(tmp_vault, "shared/_inbox/reference/c.md", sources=[], extra="project: demo\n")
    _page(tmp_vault, "shared/_inbox/reference/d.md", sources=[])

    assert {p.key for p in I.staged_pages(tmp_vault, project="demo")} == {
        "reference/a", "reference/c",
    }
    # The unattributable page is not silently handed to whoever asks first: it
    # is reachable only in the unscoped listing (`mnemo inbox --all`).
    assert "reference/d" in {p.key for p in I.staged_pages(tmp_vault)}


def test_the_archive_copies_an_older_run_left_are_not_the_queue(tmp_vault: Path):
    """Scope is ``_inbox/<type>/``, never an ``rglob`` — the #375 correction.

    34 of the real vault's 36 ``.proposed.md`` sat in ``_inbox/proposals/`` and
    ``_inbox/rejected-<run>/``. Counting them made doctor contradict the only
    tool that could act on them.
    """
    _page(tmp_vault, "shared/_inbox/proposals/leftover.md", sources=["bots/demo/x.md"])
    _page(tmp_vault, "shared/_inbox/reference/real.md", sources=["bots/demo/x.md"])

    assert [p.key for p in I.staged_pages(tmp_vault)] == ["reference/real"]


# --- promote ---------------------------------------------------------------


def test_promote_moves_the_page_and_the_ledger_with_it(tmp_vault: Path, monkeypatch):
    """The extractor's ledger is why this is a command and not a documented ``mv``.

    Left at ``inbox``, the next extraction finds the staging file gone and the
    sacred one present, flips the status itself and stages an
    ``.update-proposed.md`` for a source that never changed.
    """
    monkeypatch.setattr(I, "_rebuild_indexes", lambda _v: None)
    _page(tmp_vault, "shared/_inbox/reference/demo__x.md", sources=["bots/demo/a.md"])
    _state(tmp_vault, "reference/demo__x")
    page = I.staged_pages(tmp_vault)[0]

    result = I.promote(tmp_vault, page, project="demo")

    assert result.ok and result.state_updated
    assert (tmp_vault / "shared" / "reference" / "demo__x.md").is_file()
    assert not page.path.exists()
    entry = json.loads((tmp_vault / ".mnemo" / "extraction-state.json").read_text(
        encoding="utf-8"))["entries"]["reference/demo__x"]
    assert entry["status"] == "promoted"
    assert entry["written_hash"] != "wh", "the hash must match the bytes now on disk"


def test_promote_refuses_when_a_live_page_already_holds_the_name(tmp_vault: Path, monkeypatch):
    """Two texts, one identity — picking a winner here would drop one silently."""
    monkeypatch.setattr(I, "_rebuild_indexes", lambda _v: None)
    live = _page(tmp_vault, "shared/reference/demo__x.md", sources=["bots/demo/a.md"])
    live.write_text("---\nname: live\n---\n\nthe live text\n", encoding="utf-8")
    staged = _page(tmp_vault, "shared/_inbox/reference/demo__x.md", sources=["bots/demo/a.md"])
    page = I.staged_pages(tmp_vault)[0]

    result = I.promote(tmp_vault, page)

    assert not result.ok
    assert "already exists" in result.message
    assert staged.exists(), "a refused promote must leave the queue untouched"
    assert "the live text" in live.read_text(encoding="utf-8")


def test_promote_without_a_state_entry_says_so(tmp_vault: Path, monkeypatch):
    """Imported and hand-written pages have no entry; inventing one would hand
    the extractor a source hash no source ever produced."""
    monkeypatch.setattr(I, "_rebuild_indexes", lambda _v: None)
    _page(tmp_vault, "shared/_inbox/reference/demo__x.md", sources=["bots/demo/a.md"])

    result = I.promote(tmp_vault, I.staged_pages(tmp_vault)[0])

    assert result.ok and result.state_updated is False


def test_promote_rebuilds_the_indexes_so_recall_can_reach_it(tmp_vault: Path, monkeypatch):
    """A page nobody can retrieve until the next session start is half promoted."""
    called: list[str] = []
    monkeypatch.setattr(I, "_rebuild_indexes", lambda v: called.append(str(v)))
    _page(tmp_vault, "shared/_inbox/reference/demo__x.md", sources=["bots/demo/a.md"])

    I.promote(tmp_vault, I.staged_pages(tmp_vault)[0])

    assert called == [str(tmp_vault)]


# --- drop ------------------------------------------------------------------


def test_drop_archives_before_it_deletes(tmp_vault: Path):
    """Extraction re-derives a page from its source, so a drop is reversible
    only while that source still says the same thing."""
    _page(tmp_vault, "shared/_inbox/reference/demo__x.md", sources=["bots/demo/a.md"])
    _state(tmp_vault, "reference/demo__x")
    page = I.staged_pages(tmp_vault)[0]

    result = I.drop(tmp_vault, page, project="demo")

    assert result.ok and not page.path.exists()
    assert result.moved_to is not None and result.moved_to.is_file()
    assert "body" in result.moved_to.read_text(encoding="utf-8")
    entry = json.loads((tmp_vault / ".mnemo" / "extraction-state.json").read_text(
        encoding="utf-8"))["entries"]["reference/demo__x"]
    assert entry["status"] == "dismissed", "a dropped page must not come back on the next run"


# --- the numbers -----------------------------------------------------------


def test_stats_reports_depth_age_and_what_drained(tmp_vault: Path, monkeypatch):
    monkeypatch.setattr(I, "_rebuild_indexes", lambda _v: None)
    _page(tmp_vault, "shared/_inbox/reference/a.md", sources=["bots/demo/x.md"], age_days=10)
    _page(tmp_vault, "shared/_inbox/reference/b.md", sources=["bots/demo/x.md"], age_days=2)
    _page(tmp_vault, "shared/_inbox/reference/c.md", sources=["bots/demo/x.md"], age_days=4)

    pages = I.staged_pages(tmp_vault)
    I.record(tmp_vault, event=I.OFFERED, key=pages[0].key, project="demo")
    I.promote(tmp_vault, pages[0], project="demo")
    I.drop(tmp_vault, pages[1], project="demo")

    stats = I.stats(tmp_vault)

    assert stats["staged"] == 1
    assert stats["offered"] == 1
    assert stats["promoted"] == 1 and stats["dropped"] == 1
    assert stats["resolved"] == 2
    assert stats["median_decision_days"] == 0.0


def test_a_page_decided_before_it_was_ever_offered_has_no_latency(tmp_vault: Path):
    """The latency answers "did the offer drain this", so a page nobody was
    shown must not be counted as one that was."""
    I.record(tmp_vault, event=I.PROMOTED, key="reference/a", project="demo")

    assert I.stats(tmp_vault)["median_decision_days"] is None
    assert I.stats(tmp_vault)["promoted"] == 1


def test_stats_window_excludes_older_events(tmp_vault: Path):
    path = I.ledger_path(tmp_vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    path.write_text(
        json.dumps({"ts": old, "event": "promoted", "key": "reference/a", "project": "demo"})
        + "\n", encoding="utf-8",
    )

    assert I.stats(tmp_vault, window_days=7)["promoted"] == 0
    assert I.stats(tmp_vault, window_days=60)["promoted"] == 1


def test_an_unreadable_ledger_costs_the_numbers_not_the_command(tmp_vault: Path):
    path = I.ledger_path(tmp_vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json\n\n{\"event\": \"offered\"}\n", encoding="utf-8")

    assert I.read_ledger(tmp_vault) == []
    assert I.stats(tmp_vault)["offered"] == 0
