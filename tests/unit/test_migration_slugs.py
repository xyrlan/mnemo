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
    assert rep.stamped == 4
    assert parse_frontmatter(plain.read_text(encoding="utf-8"))["slug"] == "bingx-robot__ai-pm"
    assert parse_frontmatter(proposed.read_text(encoding="utf-8"))["slug"] == "x__y.proposed"
    assert parse_frontmatter(staged.read_text(encoding="utf-8"))["slug"] == "a__b"
    assert parse_frontmatter(feedback.read_text(encoding="utf-8"))["slug"] == "use-yarn"
