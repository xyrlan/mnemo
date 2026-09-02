"""Consolidation prompts: evidence requirement, existing-rules list, few-shot round-trip."""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract import _parse_pages_from_response
from mnemo.core.extract.prompts import build_consolidation_prompt
from mnemo.core.extract.prompts import existing_rules
from mnemo.core.extract.prompts.existing_rules import existing_rules_fragment
from mnemo.core.extract.prompts.templates.few_shot_feedback import _FEW_SHOT_FEEDBACK
from mnemo.core.extract.prompts.templates.schema import _SCHEMA_EXAMPLE
from mnemo.core.extract.prompts.templates.system_feedback import FEEDBACK_SYSTEM_PROMPT
from mnemo.core.extract.scanner import MemoryFile


def _rule(root: Path, kind: str, slug: str, name: str, sources: list[str], inbox=False):
    d = root / "shared" / ("_inbox/" + kind if inbox else kind)
    d.mkdir(parents=True, exist_ok=True)
    src = "\n".join(f"  - {s}" for s in sources)
    (d / f"{slug}.md").write_text(f"---\nname: {name}\ntype: {kind}\nsources:\n{src}\ntags:\n  - x\n---\nbody\n", encoding="utf-8")


def _mf(agent: str) -> MemoryFile:
    return MemoryFile(path=Path(f"/v/bots/{agent}/briefings/sessions/s.md"), agent=agent,
                      type="feedback", slug="briefing-s", frontmatter={"type": "briefing"},
                      body="## Corrections\n- \"use yarn not npm\" → Use yarn\n", source_hash="h")


def test_system_prompt_requires_evidence_and_slug_reuse():
    p = FEEDBACK_SYSTEM_PROMPT
    assert "evidence" in p and "## Corrections" in p
    assert "type: reference" in p or '"type": "reference"' in p
    assert "reuse" in p.lower() and "slug" in p.lower()


def test_schema_example_documents_evidence():
    assert '"evidence"' in _SCHEMA_EXAMPLE and '"quote"' in _SCHEMA_EXAMPLE


def test_existing_rules_fragment_lists_same_project_rules_by_source_count(tmp_path):
    _rule(tmp_path, "feedback", "use-yarn", "Use yarn", ["bots/a/briefings/sessions/1.md", "bots/b/briefings/sessions/2.md"])
    _rule(tmp_path, "feedback", "no-any", "No any", ["bots/a/memory/f.md"])
    _rule(tmp_path, "feedback", "other-proj", "Other", ["bots/zzz/memory/f.md"])
    _rule(tmp_path, "feedback", "staged", "Staged", ["bots/a/memory/g.md"], inbox=True)
    _rule(tmp_path, "reference", "ref", "Ref", ["bots/a/memory/r.md"])
    frag = existing_rules_fragment(tmp_path, "feedback", agents={"a"})
    lines = [l for l in frag.splitlines() if l.startswith("- ")]
    assert lines[0].startswith("- use-yarn — Use yarn")
    assert any(l.startswith("- no-any") for l in lines)
    assert any(l.startswith("- staged") for l in lines)
    assert not any("other-proj" in l or l.startswith("- ref ") for l in lines)


def test_existing_rules_fragment_caps_at_80(tmp_path):
    for i in range(90):
        _rule(tmp_path, "feedback", f"r{i:03d}", f"R{i}", ["bots/a/memory/f.md"])
    frag = existing_rules_fragment(tmp_path, "feedback", agents={"a"})
    assert sum(1 for l in frag.splitlines() if l.startswith("- ")) == 80


def test_existing_rules_fragment_empty_on_fresh_vault(tmp_path):
    assert existing_rules_fragment(tmp_path, "feedback", agents={"a"}) == ""


def test_consolidation_prompt_includes_fragment_for_chunk_agents(tmp_path):
    _rule(tmp_path, "feedback", "use-yarn", "Use yarn", ["bots/a/memory/f.md"])
    text = build_consolidation_prompt("feedback", [_mf("a")], vault_root=tmp_path)
    assert "Existing rules" in text and "- use-yarn — Use yarn" in text


def test_few_shot_example_1_round_trips_with_evidence():
    blob = _FEW_SHOT_FEEDBACK.split("Output (ONE merged page")[1].split("\n", 1)[1].split("\n\nExample 2")[0].strip()
    pages = _parse_pages_from_response(blob, "feedback")
    assert pages and pages[0].evidence and pages[0].evidence["quote"]


def test_advertised_slug_is_normalized_so_it_round_trips(tmp_path):
    """A page at Ask_Before_Refactor.md must be advertised as the slug the
    response parser produces — otherwise the echoed slug mints a duplicate."""
    _rule(tmp_path, "feedback", "Ask_Before_Refactor", "Ask before refactor", ["bots/a/memory/f.md"])
    frag = existing_rules_fragment(tmp_path, "feedback", agents={"a"})
    assert "- ask-before-refactor — Ask before refactor" in frag
    assert "Ask_Before_Refactor" not in frag


def test_collect_is_cached_until_cleared(tmp_path):
    _rule(tmp_path, "feedback", "first", "First", ["bots/a/memory/f.md"])
    existing_rules.clear_cache()
    assert "- first — First" in existing_rules_fragment(tmp_path, "feedback", agents={"a"})

    # A page written after the first scan stays invisible while cached...
    _rule(tmp_path, "feedback", "second", "Second", ["bots/a/memory/g.md"])
    assert "second" not in existing_rules_fragment(tmp_path, "feedback", agents={"a"})

    # ...and appears once the run clears the cache (as it does after apply_pages).
    existing_rules.clear_cache()
    frag = existing_rules_fragment(tmp_path, "feedback", agents={"a"})
    assert "- second — Second" in frag and "- first — First" in frag
