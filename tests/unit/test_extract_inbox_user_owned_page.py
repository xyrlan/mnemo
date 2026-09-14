"""#248: a live ``shared/<type>/`` page with no state entry is user-owned.

Pages with no entry in ``extraction-state.json`` are exactly the ones a
person put there — written by hand, ``mv``-promoted out of ``_inbox/``, or
imported. ``_apply_auto_promoted`` used to route every ``entry is None`` page
to an unconditional ``atomic_write``, replacing a reviewed page with a
single-source re-emission. Such a page must take the same door an edited
sacred page already takes: a ``.proposed.md`` sibling in ``_inbox/``.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract.inbox.apply import apply_pages
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.extract.scanner import ExtractionState

ORIGINAL = "THE ORIGINAL BODY, reviewed by a human."


def _hand_written(root: Path, type_: str = "feedback", slug: str = "hand-written") -> Path:
    live = root / "shared" / type_ / f"{slug}.md"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(
        f"---\nname: Hand written\nslug: {slug}\ntype: {type_}\n"
        "confidence: verified\nsources: []\ntags: []\n---\n\n"
        f"{ORIGINAL}\n",
        encoding="utf-8",
    )
    (root / "bots" / "proj" / "briefings").mkdir(parents=True, exist_ok=True)
    (root / "bots" / "proj" / "briefings" / "s1.md").write_text("# b\n", encoding="utf-8")
    return live


def _reemission(body: str = "A NEW BODY from one local briefing.", *, source_hash: str = "h1") -> ExtractedPage:
    return ExtractedPage(
        slug="hand-written", type="feedback", name="Re-emitted", description="d",
        body=body, source_files=["bots/proj/briefings/s1.md"], source_hash=source_hash,
    )


def test_entryless_live_page_is_never_overwritten(tmp_path):
    live = _hand_written(tmp_path)
    before = live.read_text(encoding="utf-8")
    state = ExtractionState(last_run=None, entries={}, schema_version=1)

    res = apply_pages([_reemission()], state, tmp_path, run_id="2026-09-13T00:00:00")

    assert live.read_text(encoding="utf-8") == before, "the reviewed page must survive byte for byte"
    assert res.auto_promoted == []
    sibling = tmp_path / "shared" / "_inbox" / "feedback" / "hand-written.proposed.md"
    assert sibling.exists(), "the re-emission is staged for review, not applied"
    assert "A NEW BODY" in sibling.read_text(encoding="utf-8")
    assert res.sibling_bounced == [("feedback/hand-written", str(sibling))]
    assert "feedback/hand-written" not in state.entries, "no entry is adopted for a user-owned page"


def test_entryless_live_page_second_run_does_not_churn_the_sibling(tmp_path):
    _hand_written(tmp_path)
    state = ExtractionState(last_run=None, entries={}, schema_version=1)
    apply_pages([_reemission()], state, tmp_path, run_id="2026-09-13T00:00:00")
    sibling = tmp_path / "shared" / "_inbox" / "feedback" / "hand-written.proposed.md"
    first = sibling.read_text(encoding="utf-8")

    res = apply_pages([_reemission()], state, tmp_path, run_id="2026-09-14T00:00:00")

    assert sibling.read_text(encoding="utf-8") == first, "only the run stamps moved; nothing to re-stage"
    assert res.sibling_bounced == []


def test_entryless_live_page_changed_reemission_refreshes_the_sibling(tmp_path):
    _hand_written(tmp_path)
    state = ExtractionState(last_run=None, entries={}, schema_version=1)
    apply_pages([_reemission()], state, tmp_path, run_id="2026-09-13T00:00:00")
    sibling = tmp_path / "shared" / "_inbox" / "feedback" / "hand-written.proposed.md"

    res = apply_pages(
        [_reemission("A DIFFERENT BODY.", source_hash="h2")],
        state, tmp_path, run_id="2026-09-14T00:00:00",
    )

    assert "A DIFFERENT BODY" in sibling.read_text(encoding="utf-8")
    assert res.sibling_bounced == [("feedback/hand-written", str(sibling))]
    assert ORIGINAL in (tmp_path / "shared" / "feedback" / "hand-written.md").read_text(encoding="utf-8")


def test_entryless_page_with_no_live_target_still_writes_fresh(tmp_path):
    (tmp_path / "bots" / "proj" / "briefings").mkdir(parents=True)
    state = ExtractionState(last_run=None, entries={}, schema_version=1)

    res = apply_pages([_reemission()], state, tmp_path, run_id="2026-09-13T00:00:00")

    assert res.auto_promoted == ["feedback/hand-written"]
    assert (tmp_path / "shared" / "feedback" / "hand-written.md").exists()
    assert state.entries["feedback/hand-written"].status == "auto_promoted"
