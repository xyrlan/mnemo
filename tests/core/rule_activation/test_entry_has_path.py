from pathlib import Path
from mnemo.core.rule_activation.index import build_index


def test_entry_stores_rule_path(tmp_path: Path):
    vault = tmp_path
    (vault / "shared" / "feedback").mkdir(parents=True)
    rule = vault / "shared" / "feedback" / "example.md"
    rule.write_text(
        "---\n"
        "name: Example\n"
        "description: Example rule\n"
        "type: feedback\n"
        "sources:\n"
        "  - bots/demo/memory/foo.md\n"
        "tags:\n"
        "  - demo\n"
        "---\n"
        "Body.\n"
    )
    index = build_index(vault)
    rules = index.get("rules", {})
    assert rules, "expected at least one rule in the index"
    any_entry = next(iter(rules.values()))
    assert "path" in any_entry
    assert any_entry["path"].endswith("shared/feedback/example.md")
