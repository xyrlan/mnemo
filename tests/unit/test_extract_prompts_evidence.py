"""Consolidation prompts: evidence requirement, existing-rules list, few-shot round-trip."""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract import _parse_pages_from_response
from mnemo.core.extract.prompts import build_consolidation_prompt
from mnemo.core.extract.prompts import existing_rules
from mnemo.core.extract.prompts.existing_rules import MAX_ENTRIES, existing_rules_fragment
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


def test_existing_rules_fragment_scopes_an_imported_rule_to_its_local_project(tmp_path):
    """#302: an imported page has ``sources: []`` and names its project only in
    ``projects:``. Read without that fallback it looked project-less, and a
    project-less rule is advertised to every project's chunk."""
    from mnemo.core.share import from_portable, to_portable, to_vault_page
    from tests.unit._export_fixtures import write_rule

    native = write_rule(tmp_path / "publisher", slug="use-yarn", quote="always yarn",
                        projects=("theirs",)).read_text(encoding="utf-8")
    rule = from_portable(to_portable(native, vault="v-one", project="theirs", today="2026-09-01"))
    staged = tmp_path / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    staged.parent.mkdir(parents=True)
    staged.write_text(to_vault_page(rule, project="mine", today="2026-09-13"), encoding="utf-8")

    assert "- use-yarn" in existing_rules_fragment(tmp_path, "feedback", agents={"mine"})
    assert existing_rules_fragment(tmp_path, "feedback", agents={"other"}) == ""


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


def _rule_with_body(root: Path, kind: str, slug: str, name: str, body: str,
                    sources: list[str], description: str = "") -> None:
    d = root / "shared" / kind
    d.mkdir(parents=True, exist_ok=True)
    src = "\n".join(f"  - {s}" for s in sources)
    (d / f"{slug}.md").write_text(
        f"---\nname: {name}\ntype: {kind}\ndescription: {description}\n"
        f"sources:\n{src}\ntags:\n  - x\n---\n{body}\n",
        encoding="utf-8",
    )


RECIPE = (
    "Route Geocoding through the server. Set MAPS_SERVER_API_KEY in the "
    "environment. HTTP referrer restrictions do not work for the Geocoding "
    "REST API, so the browser key cannot be reused."
)


def _chunk_file(agent: str, body: str, slug: str = "briefing-s") -> MemoryFile:
    return MemoryFile(path=Path(f"/v/bots/{agent}/briefings/sessions/s.md"), agent=agent,
                      type="reference", slug=slug, frontmatter={"type": "briefing"},
                      body=body, source_hash="h")


def test_fragment_shows_the_body_of_a_rule_the_chunk_is_about_to_restate(tmp_path):
    """The bug in #184: the model is told to reuse a slug whose text it has
    never seen, so it re-summarizes and drops the specifics."""
    existing_rules.clear_cache()
    _rule_with_body(tmp_path, "reference", "google-maps-api-key-server-side-routing",
                    "Google Maps API key server-side routing", RECIPE,
                    ["bots/a/memory/f.md"], description="Route Geocoding server-side")
    chunk = [_chunk_file("a", "Google Maps geocoding API key should be routed server side.")]
    frag = existing_rules_fragment(tmp_path, "reference", agents={"a"}, chunk=chunk)
    assert "MAPS_SERVER_API_KEY" in frag
    assert "referrer restrictions do not work" in frag


def test_fragment_omits_bodies_of_unrelated_rules(tmp_path):
    """Bodies are the expensive part: 80 of them is ~17k tokens. Only the
    rules the chunk might actually overwrite earn one."""
    existing_rules.clear_cache()
    _rule_with_body(tmp_path, "reference", "google-maps-api-key-server-side-routing",
                    "Google Maps API key server-side routing", RECIPE, ["bots/a/memory/f.md"])
    _rule_with_body(tmp_path, "reference", "postgres-connection-pooling",
                    "Postgres connection pooling", "Use pgbouncer in transaction mode.",
                    ["bots/a/memory/g.md"])
    chunk = [_chunk_file("a", "Google Maps geocoding API key should be routed server side.")]
    frag = existing_rules_fragment(tmp_path, "reference", agents={"a"}, chunk=chunk)
    assert "MAPS_SERVER_API_KEY" in frag
    assert "pgbouncer" not in frag
    # the cheap slug — name line still advertises every rule
    assert "- postgres-connection-pooling — Postgres connection pooling" in frag


def test_fragment_states_the_edit_contract_when_it_shows_a_body(tmp_path):
    """Seeing the text is half the fix; the instruction has to say what to do
    with it, or a reword is still a valid response."""
    existing_rules.clear_cache()
    _rule_with_body(tmp_path, "reference", "google-maps-api-key-server-side-routing",
                    "Google Maps API key server-side routing", RECIPE, ["bots/a/memory/f.md"])
    chunk = [_chunk_file("a", "Google Maps geocoding API key should be routed server side.")]
    frag = existing_rules_fragment(tmp_path, "reference", agents={"a"}, chunk=chunk)
    low = frag.lower()
    assert "add" in low and "restate" in low
    assert "do not emit" in low


def test_no_chunk_means_no_bodies(tmp_path):
    """Callers that pass no chunk (and the cached slug list) keep the old,
    cheap shape — no behaviour change and no token cost."""
    existing_rules.clear_cache()
    _rule_with_body(tmp_path, "reference", "google-maps-api-key-server-side-routing",
                    "Google Maps API key server-side routing", RECIPE, ["bots/a/memory/f.md"])
    frag = existing_rules_fragment(tmp_path, "reference", agents={"a"})
    assert "- google-maps-api-key-server-side-routing" in frag
    assert "MAPS_SERVER_API_KEY" not in frag


def test_relevant_body_survives_the_max_entries_cut(tmp_path):
    """MAX_ENTRIES truncates by source_count, which is a popularity filter. The
    rule a chunk is about to overwrite is typically unpopular: on the real vault
    all four of #184's re-summarized rules fell outside the top 80. Scoring only
    the listed slice would silently never quote them."""
    existing_rules.clear_cache()
    for i in range(MAX_ENTRIES + 10):
        _rule_with_body(tmp_path, "reference", f"popular-{i:03d}", f"Popular {i}",
                        "Unrelated body about billing invoices.",
                        [f"bots/a/memory/{j}.md" for j in range(5)])
    _rule_with_body(tmp_path, "reference", "google-maps-api-key-server-side-routing",
                    "Google Maps API key server-side routing", RECIPE,
                    ["bots/a/memory/lonely.md"])  # source_count 1 → cut from the list
    chunk = [_chunk_file("a", "Google Maps geocoding API key should be routed server side.")]
    frag = existing_rules_fragment(tmp_path, "reference", agents={"a"}, chunk=chunk)
    assert "MAPS_SERVER_API_KEY" in frag
    # and it is named in the list too, not quoted out of nowhere
    assert "- google-maps-api-key-server-side-routing —" in frag


def test_body_block_is_capped_so_a_huge_rule_cannot_blow_the_prompt(tmp_path):
    existing_rules.clear_cache()
    huge = " ".join(f"maps geocoding word{i}" for i in range(4000))
    _rule_with_body(tmp_path, "reference", "maps-geocoding-huge", "Maps geocoding huge",
                    huge, ["bots/a/memory/f.md"])
    chunk = [_chunk_file("a", "maps geocoding")]
    frag = existing_rules_fragment(tmp_path, "reference", agents={"a"}, chunk=chunk)
    assert len(frag) < 20000


def test_consolidation_prompt_carries_the_body_of_a_rule_the_chunk_restates(tmp_path):
    """End to end through the real entry point: the wiring is the fix. The
    fragment can quote bodies all it likes if the builder never passes files."""
    existing_rules.clear_cache()
    _rule_with_body(tmp_path, "reference", "google-maps-api-key-server-side-routing",
                    "Google Maps API key server-side routing", RECIPE, ["bots/a/memory/f.md"])
    chunk = [_chunk_file("a", "Google Maps geocoding API key should be routed server side.")]
    text = build_consolidation_prompt("reference", chunk, vault_root=tmp_path)
    assert "MAPS_SERVER_API_KEY" in text
    assert "do not emit that slug" in text


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
