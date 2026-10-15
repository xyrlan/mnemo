"""#665: a lost or corrupt ``extraction-state.json`` must not freeze the
pages the extractor promoted.

``load_state`` used to hand back no entries for a missing or corrupt file, and
an entry-less live page is user-owned (#248) — so one bad write of one file
turned every auto-promoted page into a hand-written one, and ``--force`` could
not bring it back. Every writer now keeps a replica of the file beside it, and
``load_state`` reads the replica when the file itself is gone or unreadable:
the extractor's pages come back with the ``written_hash`` they were written
with, so they are refreshed, and a page a person wrote or edited stays theirs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core.extract.inbox.apply import apply_pages
from mnemo.core.extract.inbox.io import content_hash
# The replica helpers are imported inside the tests that name them, so the
# scenario tests above them also run, and fail, against a tree without them.
from mnemo.core.extract.inbox.state_io import atomic_write_state, load_state
from mnemo.core.extract.inbox.types import ExtractedPage

KEY = "project/use-utc"


def _page(
    body: str, source_hash: str, slug: str = "use-utc", source: str = "bots/a/briefings/x.md",
    name: str = "Use UTC",
) -> ExtractedPage:
    return ExtractedPage(
        slug=slug, type="project", name=name, description="d",
        body=body, source_files=[source], source_hash=source_hash,
    )


def _target(vault: Path, slug: str = "use-utc") -> Path:
    return vault / "shared" / "project" / f"{slug}.md"


def _state_path(vault: Path) -> Path:
    return vault / ".mnemo" / "extraction-state.json"


def _corrupt(vault: Path) -> None:
    _state_path(vault).write_text("{ corrupt", encoding="utf-8")


def _lose(vault: Path) -> None:
    _state_path(vault).unlink()


def _promote(vault: Path) -> None:
    """Run 1: the extractor auto-promotes the page and saves its state."""
    (vault / "shared").mkdir()
    sp = _state_path(vault)
    state = load_state(sp)
    res = apply_pages(
        [_page("Always store timestamps in UTC.", "h1")],
        state, vault, run_id="2026-10-01T00:00:00",
    )
    atomic_write_state(state, sp)
    assert res.auto_promoted == [KEY]


@pytest.mark.parametrize("damage", [_corrupt, _lose], ids=["corrupt", "lost"])
@pytest.mark.parametrize("force", [False, True])
def test_auto_promoted_page_is_refreshed_after_state_loss(tmp_path, force, damage):
    _promote(tmp_path)
    damage(tmp_path)
    state = load_state(_state_path(tmp_path))
    assert KEY in state.entries, "the entry is rebuilt from the replica"

    res = apply_pages(
        [_page("Always store timestamps in UTC, and print them as ISO-8601.", "h2")],
        state, tmp_path, run_id="2026-10-02T00:00:00", force=force,
    )

    live = _target(tmp_path)
    assert "ISO-8601" in live.read_text(encoding="utf-8"), "the live page is refreshed"
    assert res.overwrite_safe == [KEY]
    assert res.sibling_bounced == []
    entry = state.entries[KEY]
    assert entry.status == "auto_promoted"
    assert entry.written_hash == content_hash(live)
    assert entry.source_hash == "h2"


@pytest.mark.parametrize("force", [False, True])
def test_hand_written_page_stays_untouched_after_state_loss(tmp_path, force):
    _promote(tmp_path)  # the vault has a state and a replica, with other entries
    live = _target(tmp_path, "my-rule")
    live.write_text(
        "---\nname: My rule\nslug: my-rule\ndescription: d\ntype: project\n"
        "sources: []\ntags: []\n---\n\nMy own rule, written by hand.\n",
        encoding="utf-8",
    )
    before = live.read_bytes()
    _corrupt(tmp_path)
    state = load_state(_state_path(tmp_path))

    res = apply_pages(
        [_page(
            "Prefer squash merges for feature branches.", "h9",
            slug="my-rule", source="bots/b/briefings/y.md", name="Squash merges",
        )],
        state, tmp_path, run_id="2026-10-02T00:00:00", force=force,
    )

    assert live.read_bytes() == before
    assert "project/my-rule" not in state.entries
    assert res.overwrite_safe == [] and res.auto_promoted == []
    assert [k for k, _ in res.sibling_bounced] == ["project/my-rule"]


@pytest.mark.parametrize("force", [False, True])
def test_auto_promoted_page_a_person_edited_stays_theirs_after_state_loss(tmp_path, force):
    _promote(tmp_path)
    live = _target(tmp_path)
    live.write_text(
        live.read_text(encoding="utf-8").replace("in UTC.", "in UTC, always."),
        encoding="utf-8",
    )
    before = live.read_bytes()
    _corrupt(tmp_path)
    state = load_state(_state_path(tmp_path))

    res = apply_pages(
        [_page("Always store timestamps in UTC, and print them as ISO-8601.", "h2")],
        state, tmp_path, run_id="2026-10-02T00:00:00", force=force,
    )

    assert live.read_bytes() == before
    assert [k for k, _ in res.sibling_bounced] == [KEY]


def test_a_dismissal_survives_state_loss(tmp_path):
    _promote(tmp_path)
    _target(tmp_path).unlink()
    state = load_state(_state_path(tmp_path))
    apply_pages([_page("b2", "h2")], state, tmp_path, run_id="2026-10-02T00:00:00")
    atomic_write_state(state, _state_path(tmp_path))
    assert state.entries[KEY].status == "dismissed"
    _corrupt(tmp_path)

    res = apply_pages(
        [_page("b3", "h3")], load_state(_state_path(tmp_path)), tmp_path,
        run_id="2026-10-03T00:00:00",
    )

    assert res.dismissed_skipped == [KEY]
    assert not _target(tmp_path).exists(), "the page the user deleted does not come back"


def test_the_file_is_put_back_and_the_corrupt_one_kept(tmp_path):
    _promote(tmp_path)
    sp = _state_path(tmp_path)
    good = sp.read_bytes()
    _corrupt(tmp_path)

    load_state(sp)

    assert sp.read_bytes() == good
    baks = [p for p in sp.parent.iterdir() if p.name.startswith(sp.name + ".bak.")]
    assert len(baks) == 1 and baks[0].read_text(encoding="utf-8") == "{ corrupt"


def test_with_both_copies_unreadable_the_state_is_empty(tmp_path):
    from mnemo.core.extract.inbox.state_io import replica_path

    _promote(tmp_path)
    sp = _state_path(tmp_path)
    _corrupt(tmp_path)
    replica_path(sp).write_text("[1, 2", encoding="utf-8")

    assert load_state(sp).entries == {}


def test_write_state_bytes_writes_both_copies(tmp_path):
    from mnemo.core.extract.inbox.state_io import replica_path, write_state_bytes

    sp = tmp_path / ".mnemo" / "extraction-state.json"

    write_state_bytes(sp, b'{"schema_version": 2, "entries": {}}')

    assert replica_path(sp) == sp.parent / "extraction-state.json.replica"
    assert sp.read_bytes() == replica_path(sp).read_bytes()
    assert sorted(p.name for p in sp.parent.iterdir()) == [
        "extraction-state.json", "extraction-state.json.replica",
    ]


def test_inbox_status_writer_keeps_the_replica_in_step(tmp_path):
    """``mnemo inbox promote/drop`` edits the ledger as raw JSON (#665)."""
    from mnemo.core.extract.inbox.state_io import replica_path
    from mnemo.core.inbox import _update_state_entry

    _promote(tmp_path)
    sp = _state_path(tmp_path)

    assert _update_state_entry(tmp_path, KEY, status="dismissed", page=None)

    assert json.loads(replica_path(sp).read_text(encoding="utf-8"))["entries"][KEY]["status"] == "dismissed"
    assert sp.read_bytes() == replica_path(sp).read_bytes()
