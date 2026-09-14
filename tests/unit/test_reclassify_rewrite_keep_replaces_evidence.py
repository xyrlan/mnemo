"""``_rewrite_keep`` on a page that already carries ``evidence:`` must replace
the block, not append a second top-level key (#257 re-keeps reclassify-era
pages whose evidence block reclassify itself wrote)."""
from __future__ import annotations

from mnemo.core.reclassify_apply import _rewrite_demote, _rewrite_keep
from mnemo.core.reclassify_types import split_frontmatter

TEXT = (
    "---\nname: r\ntype: feedback\nconfidence: verified\nstability: stable\n"
    "sources:\n  - bots/a/briefings/sessions/s.md\n"
    "evidence:\n  quote: 'old words here'\n  source: 'briefing: bots/a/briefings/sessions/s.md — user turns, turn 3'\n"
    "  link: 'old link'\ntags:\n  - t\n---\nbody\n"
)


def test_rewrite_keep_replaces_an_existing_evidence_block():
    out = _rewrite_keep(TEXT, "new words here", "bots/a/briefings/sessions/s.md", "new link")
    assert out.count("\nevidence:\n") == 1
    fm, body = split_frontmatter(out)
    assert fm["evidence"] == {"quote": "new words here", "source": "bots/a/briefings/sessions/s.md", "link": "new link"}
    assert fm["tags"] == ["t"], "keys after the old block survive"
    assert body.strip() == "body"


def test_rewrite_demote_drops_the_pages_own_confidence_line():
    out = _rewrite_demote(TEXT)
    assert out.count("\nconfidence:") == 1
    fm, _ = split_frontmatter(out)
    assert fm["type"] == "reference"
    assert fm["confidence"] == "inferred"
    assert fm["demoted_from"] == "feedback"
