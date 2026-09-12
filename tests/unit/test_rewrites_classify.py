"""Classification of staged ``.proposed.md`` rewrites (#159).

The real vault's 35 proposals split 11 / 18 / 6 across insert_only / mixed /
full_rewrite. One accept semantic cannot serve all three: a plain ``mv``
discards live content in the 11, and a plain append makes the 6 assert both
``MARKETPLACE_ENABLED = false`` and ``= true``.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.rewrites import classify as C


def _write(p: Path, fm: str, body: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{fm}\n---\n\n{body}", encoding="utf-8")
    return p


def _pair(vault: Path, slug: str, live_body: str, prop_body: str, *, page_type: str = "project"):
    fm = f"name: n\nslug: {slug}\ntype: {page_type}\nsources:\n  - bots/a/memory/{slug}.md"
    _write(vault / "shared" / page_type / f"{slug}.md", fm, live_body)
    _write(vault / "shared" / "_inbox" / page_type / f"{slug}.proposed.md", fm, prop_body)


def test_appended_lines_classify_as_insert_only(tmp_vault: Path):
    _pair(
        tmp_vault,
        "a__x",
        "line one\nline two\n",
        "line one\nline two\nline three\n",
    )

    rewrites = C.classify(tmp_vault)

    assert len(rewrites) == 1
    r = rewrites[0]
    assert r.kind == "insert_only"
    assert r.keep_ratio == 1.0
    assert r.inserted_lines == 1
    assert r.dropped_lines == 0
    assert r.key == "project/a__x"
