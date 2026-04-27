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


def _seed_briefing(vault: Path, project: str, session_id: str, body: str) -> Path:
    d = vault / "bots" / project / "briefings" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.md"
    p.write_text(
        "---\nproject: " + project + "\ntype: briefing\n---\n\n" + body + "\n",
        encoding="utf-8",
    )
    return p


def test_briefings_get_spawned_rules_back_edges(tmp_vault, monkeypatch, capsys):
    """Briefings referenced by rules' ``sources:`` should gain a
    ``## Spawned rules`` section listing wikilinks back to those rules."""
    proj = "demo-project"
    b1 = _seed_briefing(tmp_vault, proj, "session-aaa", "First session log.")
    b2 = _seed_briefing(tmp_vault, proj, "session-bbb", "Second session log.")
    rel_b1 = b1.relative_to(tmp_vault).as_posix()
    rel_b2 = b2.relative_to(tmp_vault).as_posix()
    _seed_rule(tmp_vault, "rule-x", sources=[rel_b1, rel_b2])
    _seed_rule(tmp_vault, "rule-y", sources=[rel_b2])
    _seed_rule(tmp_vault, "rule-z", sources=[])  # orphan; should not appear

    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    rc = cli.main(["regen-graph-edges"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "2 briefing" in out

    # Briefing 1 → only rule-x references it
    text_b1 = b1.read_text()
    assert GRAPH_SECTION_MARKER in text_b1
    assert "## Spawned rules" in text_b1
    assert "[[rule-x]]" in text_b1
    assert "[[rule-y]]" not in text_b1

    # Briefing 2 → both rule-x and rule-y reference it (sorted, dedup'd)
    text_b2 = b2.read_text()
    assert "[[rule-x]]" in text_b2
    assert "[[rule-y]]" in text_b2
    assert text_b2.index("[[rule-x]]") < text_b2.index("[[rule-y]]")


def test_briefing_with_no_referencing_rules_has_no_section(tmp_vault, monkeypatch):
    """A briefing referenced by zero rules should not get a Sources section
    (an empty list collapses to no section, idempotent)."""
    b = _seed_briefing(tmp_vault, "demo", "lonely", "Lonely briefing.")
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    cli.main(["regen-graph-edges"])
    text = b.read_text()
    assert GRAPH_SECTION_MARKER not in text
    assert "Spawned rules" not in text


def test_briefing_section_is_idempotent(tmp_vault, monkeypatch):
    """Running regen twice in a row leaves briefings byte-identical."""
    proj = "demo"
    b = _seed_briefing(tmp_vault, proj, "abc", "Body.")
    rel = b.relative_to(tmp_vault).as_posix()
    _seed_rule(tmp_vault, "rule-x", sources=[rel])
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    cli.main(["regen-graph-edges"])
    snapshot = b.read_text()
    cli.main(["regen-graph-edges"])
    assert b.read_text() == snapshot


def test_briefing_reader_strips_graph_section_before_hash(tmp_vault):
    """Hash must be computed AFTER stripping the graph section so refreshing
    back-edges does not invalidate extractor state and trigger an LLM re-run."""
    from mnemo.core.extract.scanner import _read_briefing_file

    b = _seed_briefing(tmp_vault, "demo", "s1", "Body content.")
    before = _read_briefing_file(b, agent="demo")

    # Append a fake graph section (simulating a regen-graph-edges run).
    b.write_text(
        b.read_text()
        + f"\n{GRAPH_SECTION_MARKER}\n## Spawned rules\n- [[rule-x]]\n",
        encoding="utf-8",
    )
    after = _read_briefing_file(b, agent="demo")

    assert before.source_hash == after.source_hash
    assert "Spawned rules" not in after.body
    assert "[[rule-x]]" not in after.body


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
