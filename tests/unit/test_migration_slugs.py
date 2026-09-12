"""#114: ``stamp_slugs`` writes ``slug:`` into legacy rule pages."""
from mnemo.core.filters import parse_frontmatter
from mnemo.core.migrations import slugs

LEGACY = "---\nname: Use Yarn\ndescription: d\ntype: feedback\n---\nbody\n"


def _page(vault, rel, text=LEGACY):
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_stamp_inserts_normalized_stem_after_name(tmp_path):
    p = _page(tmp_path, "shared/feedback/Use_Yarn.md")
    rep = slugs.stamp_slugs(tmp_path)
    assert rep.stamped == 1
    text = p.read_text(encoding="utf-8")
    assert text.splitlines()[1:3] == ["name: Use Yarn", "slug: use-yarn"]
    assert text.endswith("---\nbody\n")


def test_stamp_is_idempotent_and_respects_existing_slug(tmp_path):
    _page(tmp_path, "shared/feedback/a.md")
    _page(tmp_path, "shared/feedback/b.md", "---\nname: B\nslug: custom\n---\n")
    assert slugs.stamp_slugs(tmp_path).stamped == 1
    assert slugs.stamp_slugs(tmp_path).stamped == 0
    assert parse_frontmatter((tmp_path / "shared/feedback/b.md").read_text(encoding="utf-8"))["slug"] == "custom"


def test_stamp_skips_archive_and_unparsable(tmp_path):
    _page(tmp_path, "shared/_archive/reclassify-x/originals/feedback/a.md")
    _page(tmp_path, "shared/feedback/broken.md", "no frontmatter\n")
    _page(tmp_path, "shared/_inbox/feedback/c.md")
    rep = slugs.stamp_slugs(tmp_path)
    assert rep.stamped == 1 and rep.scanned == 2
    assert [p.name for p, _ in rep.skipped] == ["broken.md"]


def test_stamp_without_name_line_puts_slug_first(tmp_path):
    p = _page(tmp_path, "shared/feedback/a.md", "---\ntype: feedback\n---\n")
    slugs.stamp_slugs(tmp_path)
    assert p.read_text(encoding="utf-8").startswith("---\nslug: a\ntype: feedback\n")


def test_dry_run_changes_nothing(tmp_path):
    p = _page(tmp_path, "shared/feedback/a.md")
    assert slugs.stamp_slugs(tmp_path, dry_run=True).stamped == 1
    assert p.read_text(encoding="utf-8") == LEGACY


def test_marker_roundtrip(tmp_path):
    assert not slugs.marker_present(tmp_path)
    slugs.write_marker(tmp_path)
    assert slugs.marker_present(tmp_path)
    assert (tmp_path / slugs.MARKER_REL).read_text(encoding="utf-8") == "1\n"


def test_project_pages_keep_their_composite_stem(tmp_path):
    # ``promote._project_slug`` builds ``<agent>__<slug>`` and the learned
    # ledger records that composite verbatim; normalising ``__`` to ``-``
    # would recreate the ledger-vs-index mismatch the migration exists to fix.
    plain = _page(tmp_path, "shared/project/bingx-robot__ai-pm.md")
    proposed = _page(tmp_path, "shared/project/x__y.proposed.md")
    staged = _page(tmp_path, "shared/_inbox/project/a__b.md")
    feedback = _page(tmp_path, "shared/feedback/Use_Yarn.md")
    rep = slugs.stamp_slugs(tmp_path)
    assert rep.stamped == 3
    assert parse_frontmatter(plain.read_text(encoding="utf-8"))["slug"] == "bingx-robot__ai-pm"
    assert parse_frontmatter(staged.read_text(encoding="utf-8"))["slug"] == "a__b"
    assert parse_frontmatter(feedback.read_text(encoding="utf-8"))["slug"] == "use-yarn"
    # A .proposed.md sibling is a staged rewrite, not a page: stamping it wrote
    # `slug: x__y.proposed`, minting a second identity for a rule that already
    # has one. It is skipped now, along with every other walker (#155).
    assert "slug:" not in proposed.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# #179: a bulk rewriter owns the bytes it writes, so it must advance
# ``written_hash`` for the entry it just rewrote — the way
# ``reclassify_apply.py`` already does. Without this every stamped page reads
# as "user edited" to ``extract/inbox/branches/*`` and ``promote.py``, which is
# the condition that stages a ``.proposed.md`` instead of updating in place.
# ---------------------------------------------------------------------------

from mnemo.core.extract.inbox.io import content_hash
from mnemo.core.extract.inbox.state_io import atomic_write_state, load_state
from mnemo.core.extract.scanner import ExtractionState, StateEntry

STATE_REL = ".mnemo/extraction-state.json"


def _state_with(vault, key, page_path):
    state = ExtractionState(last_run=None, entries={
        key: StateEntry(
            source_files=["bots/proj/memory/project_thing.md"],
            source_hash="s1",
            written_hash=content_hash(page_path.read_text(encoding="utf-8")),
            written_at="r0",
            status="direct",
        ),
    })
    atomic_write_state(state, vault / STATE_REL)
    return state


def test_stamping_advances_written_hash_for_the_page_it_rewrites(tmp_path):
    p = _page(tmp_path, "shared/project/proj__thing.md")
    _state_with(tmp_path, "project/proj__thing", p)

    slugs.stamp_slugs(tmp_path)

    entry = load_state(tmp_path / STATE_REL).entries["project/proj__thing"]
    assert entry.written_hash == content_hash(p.read_text(encoding="utf-8")), (
        "the migration owns these bytes; a stale written_hash makes the page "
        "read as user-edited and stages a bogus .proposed.md"
    )


def test_dry_run_leaves_the_state_alone(tmp_path):
    p = _page(tmp_path, "shared/project/proj__thing.md")
    before = _state_with(tmp_path, "project/proj__thing", p).entries[
        "project/proj__thing"].written_hash

    slugs.stamp_slugs(tmp_path, dry_run=True)

    after = load_state(tmp_path / STATE_REL).entries["project/proj__thing"]
    assert after.written_hash == before
    assert "slug:" not in p.read_text(encoding="utf-8")


def test_reconciles_pages_a_previous_run_already_stamped(tmp_path):
    """The marker is already on disk for real vaults, so the historical drift
    has to heal even when this run stamps nothing (#179)."""
    # What a pre-fix vault looks like: the page carries the stamp an earlier
    # run wrote, while written_hash still records the bytes from *before* it.
    pre_stamp = "---\nname: T\ntype: project\n---\nbody\n"
    p = _page(tmp_path, "shared/project/proj__thing.md",
              "---\nname: T\nslug: proj__thing\ntype: project\n---\nbody\n")
    state = ExtractionState(last_run=None, entries={
        "project/proj__thing": StateEntry(
            source_files=["bots/proj/memory/project_thing.md"],
            source_hash="s1",
            written_hash=content_hash(pre_stamp),
            written_at="r0",
            status="direct",
        ),
    })
    atomic_write_state(state, tmp_path / STATE_REL)

    rep = slugs.stamp_slugs(tmp_path)

    assert rep.stamped == 0, "nothing to stamp; only the state needs healing"
    entry = load_state(tmp_path / STATE_REL).entries["project/proj__thing"]
    assert entry.written_hash == content_hash(p.read_text(encoding="utf-8"))
    assert rep.reconciled == 1


def test_a_genuine_user_edit_is_not_reconciled(tmp_path):
    """The whole point of written_hash is to notice real edits. Healing must
    not paper over a page the user actually changed."""
    p = _page(tmp_path, "shared/project/proj__thing.md",
              "---\nname: T\nslug: proj__thing\ntype: project\n---\nbody\n")
    state = ExtractionState(last_run=None, entries={
        "project/proj__thing": StateEntry(
            source_files=["bots/proj/memory/project_thing.md"],
            source_hash="s1",
            written_hash=content_hash(p.read_text(encoding="utf-8")),
            written_at="r0",
            status="direct",
        ),
    })
    atomic_write_state(state, tmp_path / STATE_REL)

    # The user edits the page by hand, and no migration touches it.
    p.write_text("---\nname: T\nslug: proj__thing\ntype: project\n---\nEDITED\n",
                 encoding="utf-8")
    rep = slugs.stamp_slugs(tmp_path)

    entry = load_state(tmp_path / STATE_REL).entries["project/proj__thing"]
    assert entry.written_hash != content_hash(p.read_text(encoding="utf-8"))
    assert rep.reconciled == 0


def test_reconciles_a_page_carrying_both_bulk_rewrites(tmp_path):
    """Pages written before 2026-09-02 carry the slug stamp *and* the #161/#163
    `sources:` relativization. Undoing only one still misses — and on the real
    vault those are exactly the 179 entries whose source is dirty today, so
    composing the two reversals is what disarms them (#179)."""
    pre_both = (
        "---\nname: T\ntype: project\nsources:\n"
        f"  - {tmp_path.resolve()}/bots/proj/memory/project_thing.md\n"
        "---\nbody\n"
    )
    p = _page(tmp_path, "shared/project/proj__thing.md",
              "---\nname: T\nslug: proj__thing\ntype: project\nsources:\n"
              "  - bots/proj/memory/project_thing.md\n---\nbody\n")
    state = ExtractionState(last_run=None, entries={
        "project/proj__thing": StateEntry(
            source_files=["bots/proj/memory/project_thing.md"],
            source_hash="s1",
            written_hash=content_hash(pre_both),
            written_at="r0",
            status="direct",
        ),
    })
    atomic_write_state(state, tmp_path / STATE_REL)

    rep = slugs.stamp_slugs(tmp_path)

    assert rep.reconciled == 1
    entry = load_state(tmp_path / STATE_REL).entries["project/proj__thing"]
    assert entry.written_hash == content_hash(p.read_text(encoding="utf-8"))


def test_an_edit_on_top_of_a_migrated_page_is_not_reconciled(tmp_path):
    """Composing reversals must not become a way to wave through real edits:
    undoing both rewrites still has to reproduce the recorded bytes exactly."""
    pre_both = (
        "---\nname: T\ntype: project\nsources:\n"
        f"  - {tmp_path.resolve()}/bots/proj/memory/project_thing.md\n"
        "---\nbody\n"
    )
    _page(tmp_path, "shared/project/proj__thing.md",
          "---\nname: T\nslug: proj__thing\ntype: project\nsources:\n"
          "  - bots/proj/memory/project_thing.md\n---\nEDITED BY HAND\n")
    state = ExtractionState(last_run=None, entries={
        "project/proj__thing": StateEntry(
            source_files=["bots/proj/memory/project_thing.md"],
            source_hash="s1",
            written_hash=content_hash(pre_both),
            written_at="r0",
            status="direct",
        ),
    })
    atomic_write_state(state, tmp_path / STATE_REL)

    assert slugs.stamp_slugs(tmp_path).reconciled == 0
