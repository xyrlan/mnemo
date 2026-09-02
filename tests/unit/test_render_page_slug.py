"""#114: rendered rule pages carry ``slug:`` so every consumer keys the same id."""
from mnemo.core.extract.inbox import ExtractedPage
from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.filters import derive_rule_slug, parse_frontmatter


def test_render_page_writes_slug_after_name():
    page = ExtractedPage(
        slug="use-yarn-not-npm",
        type="feedback",
        name="Use yarn",
        description="d",
        body="b",
        source_files=["bots/a/briefings/sessions/x.md"],
        source_hash="h",
    )
    text = _render_page(page, run_id="r", auto_promoted=True)
    lines = text.splitlines()
    assert lines[1] == "name: Use yarn" and lines[2] == "slug: use-yarn-not-npm"
    fm = parse_frontmatter(text)
    assert derive_rule_slug(fm, "use-yarn-not-npm") == "use-yarn-not-npm"
