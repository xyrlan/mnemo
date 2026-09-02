from pathlib import Path

from mnemo.cli.commands import list_enforced as le


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "shared" / "feedback").mkdir(parents=True)
    (vault / "shared" / "project").mkdir(parents=True)
    (vault / "shared" / "feedback" / "blocks-curl.md").write_text(
        "---\n"
        "name: Block curl example.com\n"
        "description: x\n"
        "type: feedback\n"
        "sources:\n"
        "  - bots/a/memory/b.md\n"
        "tags:\n"
        "  - demo\n"
        "enforce:\n"
        "  tool: Bash\n"
        "  deny_pattern: 'curl .*example\\.com'\n"
        "  reason: 'blocked'\n"
        "---\n"
        "Body.\n"
    )
    (vault / "shared" / "feedback" / "plain-rule.md").write_text(
        "---\nname: plain\ndescription: x\ntype: feedback\n"
        "sources:\n  - bots/a/memory/b.md\ntags:\n  - demo\n---\nBody\n"
    )
    return vault


def test_list_enforced_prints_rules(tmp_path: Path, capsys):
    vault = _make_vault(tmp_path)
    rc = le.run_list_enforced(vault)
    assert rc == 0
    out = capsys.readouterr().out
    assert "blocks-curl.md" in out
    assert "curl .*example" in out
    assert "plain-rule.md" not in out


def test_list_enforced_empty_vault(tmp_path: Path, capsys):
    vault = tmp_path / "vault"
    (vault / "shared").mkdir(parents=True)
    rc = le.run_list_enforced(vault)
    assert rc == 0
    out = capsys.readouterr().out
    assert "no enforce" in out.lower() or out.strip() == ""


def test_list_enforced_ignores_archive(tmp_path: Path, capsys):
    """#120: reclassify originals under shared/_archive are not live rules."""
    vault = _make_vault(tmp_path)
    archived = vault / "shared" / "_archive" / "reclassify-r" / "originals" / "feedback" / "old-curl.md"
    archived.parent.mkdir(parents=True)
    archived.write_text(
        (vault / "shared" / "feedback" / "blocks-curl.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    rc = le.run_list_enforced(vault)
    assert rc == 0
    out = capsys.readouterr().out
    assert "blocks-curl.md" in out
    assert "old-curl.md" not in out
    assert "_archive" not in out
