from pathlib import Path

import pytest

from mnemo.cli.commands import disable_rule as dr


def test_disable_rule_sets_disabled_true(tmp_path: Path, capsys):
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
        "Body line 1.\nBody line 2.\n", encoding="utf-8"
    )
    rc = dr.run_disable_rule(vault, slug="example-rule")
    assert rc == 0
    text = rule.read_text(encoding="utf-8")
    assert "disabled: true" in text.split("---", 2)[1]
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
        "disabled: true\n"
        "---\nBody\n", encoding="utf-8"
    )
    rc = dr.run_disable_rule(vault, slug="x")
    assert rc == 0
    assert rule.read_text(encoding="utf-8").count("disabled: true") == 1


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
    assert "disabled: true" not in archived.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert "not found" in (captured.out + captured.err).lower()


def test_disable_rule_resolves_display_name_after_slug_migration(tmp_path: Path, capsys):
    """#114: a migrated page carries ``slug:``, so ``derive_rule_slug`` yields
    the kebab slug — the display name must still resolve for muscle memory."""
    vault = tmp_path
    rule = vault / "shared" / "feedback" / "use-yarn.md"
    rule.parent.mkdir(parents=True)
    page = (
        "---\n"
        "name: Use Yarn\n"
        "slug: use-yarn\n"
        "type: feedback\n"
        "---\n"
        "body\n"
    )
    rule.write_text(page, encoding="utf-8")

    assert dr._find_rule_file(vault, "use-yarn") == rule
    assert dr._find_rule_file(vault, "Use Yarn") == rule

    rc = dr.run_disable_rule(vault, slug="Use Yarn")
    assert rc == 0
    assert "disabled: true" in rule.read_text(encoding="utf-8")
    assert "disabled: shared/feedback/use-yarn.md" in capsys.readouterr().out


def test_disable_rule_exact_slug_wins_over_name_collision(tmp_path: Path):
    """A page whose display name equals another page's slug must lose to the
    exact stem/slug hit, regardless of walk order."""
    vault = tmp_path
    fb = vault / "shared" / "feedback"
    fb.mkdir(parents=True)
    # Sorted first, but only matches by name.
    (fb / "aaa.md").write_text(
        "---\nname: use-yarn\nslug: aaa\ntype: feedback\n---\nbody\n", encoding="utf-8"
    )
    real = fb / "use-yarn.md"
    real.write_text(
        "---\nname: Use Yarn\nslug: use-yarn\ntype: feedback\n---\nbody\n", encoding="utf-8"
    )

    assert dr._find_rule_file(vault, "use-yarn") == real


# --- #629: the veto has to reach every surface that acts on a rule ---------

_PROJECT = "demo"


def _page(slug: str, tag: str, *, enforce: bool = False) -> str:
    """A promoted page the way the vault holds them: ``runtime: false`` is
    promote's stamp on every project page, not a veto."""
    lines = [
        "---",
        f"name: {slug}",
        f"slug: {slug}",
        f"description: never {tag} the build dir",
        "type: feedback",
        "runtime: false",
        "sources:",
        f"  - bots/{_PROJECT}/memory/{slug}.md",
        "tags:",
        f"  - {tag}",
    ]
    if enforce:
        lines += ["enforce:", "  tool: Bash", "  deny_pattern: 'rm -rf'", "  reason: no rm -rf"]
    return "\n".join(lines) + "\n---\n" + f"Never {tag} the build directory with rm -rf.\n"


def _surfaces(vault: Path, slug: str) -> dict:
    """What each consumer reads, from the indexes on disk — the cached copies
    PreToolUse and the reflex actually load, not a fresh build."""
    from mnemo.core import rule_activation
    from mnemo.core.mcp.tools import list_rules_by_topic
    from mnemo.core.reflex import index as reflex_index

    act = rule_activation.load_index(vault)
    hit = rule_activation.match_bash_enforce(act, _PROJECT, "rm -rf build")
    rdoc = (reflex_index.load_index(vault) or {}).get("docs", {}).get(slug)
    listed = list_rules_by_topic(vault, "cleanup", scope="project", project=_PROJECT)
    return {
        "blocks": hit is not None and hit.slug == slug,
        "reflex": rdoc is not None and not rdoc.get("retired"),
        "listed": slug in [m["slug"] for m in listed],
    }


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    from mnemo.core import config as cfg_mod
    from mnemo.core import rule_activation
    from mnemo.core.reflex import index as reflex_index

    vault = tmp_path / "vault"
    fb = vault / "shared" / "feedback"
    fb.mkdir(parents=True)
    (fb / "no-rm-rf.md").write_text(_page("no-rm-rf", "cleanup", enforce=True), encoding="utf-8")
    (fb / "keep-logs.md").write_text(_page("keep-logs", "cleanup"), encoding="utf-8")
    # The indexes a session start already wrote: disabling must not wait for the next one.
    rule_activation.write_index(vault, rule_activation.build_index(vault))
    reflex_index.write_index(vault, reflex_index.build_index(vault))
    monkeypatch.setattr(cfg_mod, "load_config", lambda: {"vaultRoot": str(vault)})
    from mnemo.core import paths
    monkeypatch.setattr(paths, "vault_root", lambda _cfg=None: vault)
    return vault


def test_disable_rule_cli_drops_the_rule_from_every_surface(vault: Path, capsys):
    from mnemo.cli import main
    from mnemo.core.mcp.tools import read_mnemo_rule

    live = {"blocks": True, "reflex": True, "listed": True}
    assert _surfaces(vault, "no-rm-rf") == live

    assert main(["disable-rule", "no-rm-rf"]) == 0
    out = capsys.readouterr().out
    assert "disabled: shared/feedback/no-rm-rf.md" in out
    assert "mnemo enable-rule no-rm-rf" in out

    assert _surfaces(vault, "no-rm-rf") == {"blocks": False, "reflex": False, "listed": False}
    # The neighbour carries the same promote stamp and was never vetoed.
    assert _surfaces(vault, "keep-logs")["reflex"] is True
    assert _surfaces(vault, "keep-logs")["listed"] is True

    assert main(["enable-rule", "no-rm-rf"]) == 0
    assert "enabled: shared/feedback/no-rm-rf.md" in capsys.readouterr().out
    assert _surfaces(vault, "no-rm-rf") == live
    assert "disabled" not in (vault / "shared" / "feedback" / "no-rm-rf.md").read_text(encoding="utf-8")
    assert read_mnemo_rule(vault, "no-rm-rf", scope="vault") is not None


def test_promote_runtime_false_alone_keeps_a_page_live(vault: Path):
    """``runtime: false`` is on every promoted project page (1,194 on the
    maintainer's vault); it must never read as a veto."""
    assert _surfaces(vault, "keep-logs") == {"blocks": False, "reflex": True, "listed": True}
    assert _surfaces(vault, "no-rm-rf")["blocks"] is True


def test_enable_rule_on_a_live_rule_is_a_no_op(vault: Path, capsys):
    page = vault / "shared" / "feedback" / "keep-logs.md"
    before = page.read_text(encoding="utf-8")
    assert dr.run_enable_rule(vault, slug="keep-logs") == 0
    assert "not disabled: shared/feedback/keep-logs.md" in capsys.readouterr().out
    assert page.read_text(encoding="utf-8") == before


def test_enable_rule_unknown_slug_errors(vault: Path, capsys):
    assert dr.run_enable_rule(vault, slug="nope") == 2
    assert "not found" in capsys.readouterr().err
