from pathlib import Path

from mnemo.cli.commands import disable_rule as dr


def test_disable_rule_sets_runtime_false(tmp_path: Path, capsys):
    vault = tmp_path / "vault"
    (vault / "shared" / "feedback").mkdir(parents=True)
    rule = vault / "shared" / "feedback" / "example-rule.md"
    rule.write_text(
        "---\n"
        "name: Example rule\n"
        "description: example\n"
        "type: feedback\n"
        "sources:\n"
        "  - bots/demo/memory/foo.md\n"
        "tags:\n"
        "  - demo\n"
        "---\n"
        "Body line 1.\nBody line 2.\n"
    )
    rc = dr.run_disable_rule(vault, slug="example-rule")
    assert rc == 0
    text = rule.read_text()
    assert "runtime: false" in text.split("---", 2)[1]
    assert "Body line 1." in text   # body untouched


def test_disable_rule_unknown_slug_errors(tmp_path: Path, capsys):
    vault = tmp_path / "vault"
    (vault / "shared").mkdir(parents=True)
    rc = dr.run_disable_rule(vault, slug="does-not-exist")
    assert rc != 0
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert "not found" in out.lower()


def test_disable_rule_idempotent(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "shared" / "feedback").mkdir(parents=True)
    rule = vault / "shared" / "feedback" / "x.md"
    rule.write_text(
        "---\nname: X\ndescription: x\ntype: feedback\n"
        "sources:\n  - bots/a/memory/b.md\n"
        "tags:\n  - t\n"
        "runtime: false\n"
        "---\nBody\n"
    )
    rc = dr.run_disable_rule(vault, slug="x")
    assert rc == 0
    assert rule.read_text().count("runtime: false") == 1


def test_disable_rule_ignores_archive(tmp_path: Path, capsys):
    """#120: a slug that only survives as a reclassify original is 'not found'."""
    vault = tmp_path / "vault"
    archived = vault / "shared" / "_archive" / "reclassify-r" / "originals" / "feedback" / "old-rule.md"
    archived.parent.mkdir(parents=True)
    archived.write_text(
        "---\nname: old-rule\ndescription: x\ntype: feedback\n"
        "sources:\n  - bots/a/memory/b.md\ntags:\n  - t\n---\nBody\n",
        encoding="utf-8",
    )
    rc = dr.run_disable_rule(vault, slug="old-rule")
    assert rc != 0
    assert "runtime: false" not in archived.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert "not found" in (captured.out + captured.err).lower()
