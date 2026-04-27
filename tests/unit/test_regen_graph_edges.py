"""`mnemo regen-graph-edges` — idempotent refresh of the trailing
Sources wikilink section on every shared rule."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mnemo import cli
from mnemo.cli.commands.regen_graph_edges import _refresh_one
from mnemo.core.text_utils import GRAPH_SECTION_MARKER, strip_graph_section


def _seed_rule(vault: Path, slug: str, *, sources: list[str], extra_body: str = "") -> Path:
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    sources_yaml = "\n".join(f"  - {s}" for s in sources)
    text = (
        "---\n"
        f"name: {slug}\n"
        f"description: desc for {slug}\n"
        "type: feedback\n"
        "tags:\n  - x\n"
        "sources:\n"
        f"{sources_yaml}\n"
        "---\n\n"
        f"Body for {slug}.\n"
    )
    if extra_body:
        text += extra_body
    p = d / f"{slug}.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_refresh_one_appends_section_when_missing(tmp_vault):
    md = _seed_rule(tmp_vault, "alpha",
                    sources=["bots/proj/briefings/sessions/abc.md",
                             "bots/proj/briefings/sessions/def.md"])
    assert _refresh_one(md) is True
    after = md.read_text()
    assert GRAPH_SECTION_MARKER in after
    assert "[[bots/proj/briefings/sessions/abc]]" in after
    assert "[[bots/proj/briefings/sessions/def]]" in after


def test_refresh_one_is_idempotent(tmp_vault):
    md = _seed_rule(tmp_vault, "beta", sources=["bots/p/m/x.md"])
    _refresh_one(md)
    snapshot = md.read_text()
    assert _refresh_one(md) is False
    assert md.read_text() == snapshot


def test_refresh_one_replaces_stale_section(tmp_vault):
    """Sources changed in frontmatter → graph section must reflect them."""
    md = _seed_rule(tmp_vault, "gamma", sources=["bots/p/m/old.md"])
    _refresh_one(md)
    # Mutate sources in-place by rewriting the file.
    text = md.read_text()
    head = text[:text.find(GRAPH_SECTION_MARKER)]
    head = head.replace("bots/p/m/old.md", "bots/p/m/new.md")
    md.write_text(head + text[text.find(GRAPH_SECTION_MARKER):], encoding="utf-8")

    assert _refresh_one(md) is True
    after = md.read_text()
    assert "[[bots/p/m/new]]" in after
    assert "[[bots/p/m/old]]" not in after
    assert after.count(GRAPH_SECTION_MARKER) == 1


def test_refresh_one_omits_section_when_no_sources(tmp_vault):
    md = _seed_rule(tmp_vault, "delta", sources=[])
    _refresh_one(md)
    assert GRAPH_SECTION_MARKER not in md.read_text()


def test_refresh_one_preserves_body(tmp_vault):
    """Body content above the marker MUST NOT be modified."""
    md = _seed_rule(tmp_vault, "epsilon",
                    sources=["bots/p/m/x.md"],
                    extra_body="\nMore body text with **markdown**.\n")
    original_body = strip_graph_section(md.read_text())
    _refresh_one(md)
    refreshed_body = strip_graph_section(md.read_text())
    assert refreshed_body == original_body


def test_cli_regen_graph_edges_command(tmp_vault, monkeypatch, capsys):
    _seed_rule(tmp_vault, "rule-a", sources=["bots/p/m/a.md"])
    _seed_rule(tmp_vault, "rule-b", sources=["bots/p/m/b.md"])
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    rc = cli.main(["regen-graph-edges"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "scanned 2" in out
    assert "refreshed 2" in out
    # Both files now have the section.
    for slug in ["rule-a", "rule-b"]:
        text = (tmp_vault / "shared" / "feedback" / f"{slug}.md").read_text()
        assert GRAPH_SECTION_MARKER in text
