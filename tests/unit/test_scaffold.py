from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.install import scaffold


def test_scaffold_creates_full_tree(tmp_path: Path):
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    assert (vault / "HOME.md").exists()
    assert (vault / "README.md").exists()
    assert (vault / "mnemo.config.json").exists()
    assert (vault / ".obsidian" / "snippets" / "graph-dark-gold.css").exists()
    assert (vault / "bots").is_dir()
    # Auto-populated Tier 2 — created by mnemo extract, pre-created for
    # stable Obsidian navigation on day one.
    assert (vault / "shared" / "feedback").is_dir()
    assert (vault / "shared" / "user").is_dir()
    assert (vault / "shared" / "reference").is_dir()
    assert (vault / "shared" / "project").is_dir()
    # v0.4.1: v0.1 vestigial user-curated dirs (people/companies/decisions) are
    # no longer created — they were always empty ghost towns in practice.
    assert not (vault / "shared" / "people").exists()
    assert not (vault / "shared" / "companies").exists()
    assert not (vault / "shared" / "decisions").exists()
    # v0.4: wiki/ is dead — the dashboard lives inside HOME.md instead.
    assert not (vault / "wiki").exists()


def test_scaffold_does_not_create_stale_plural_projects_dir(tmp_path: Path):
    """The extraction pipeline writes to `shared/project/` (singular) via
    promote.py. The old scaffold created `shared/projects/` (plural) which
    stayed empty forever and confused users. Don't create it."""
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    assert not (vault / "shared" / "projects").exists(), (
        "shared/projects/ (plural) is the legacy empty dir; extraction writes "
        "to shared/project/ (singular), which is what scaffold should create"
    )


def test_home_template_describes_auto_populated_types(tmp_path: Path):
    """HOME.md must accurately describe the four extraction-populated Tier 2
    categories (feedback, user, reference, project), not only the manual ones."""
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    home = (vault / "HOME.md").read_text(encoding="utf-8")
    for wikilink in ("shared/feedback", "shared/user", "shared/reference", "shared/project"):
        assert f"[[{wikilink}]]" in home, (
            f"HOME.md must link to [[{wikilink}]] — auto-populated Tier 2 dir"
        )
    # And the old broken plural pointer must be gone
    assert "[[shared/projects]]" not in home, (
        "HOME.md still points to the legacy empty shared/projects/ dir"
    )


def test_home_template_ships_dashboard_block_skeleton(tmp_path: Path):
    """v0.4: fresh HOME.md must contain the managed dashboard block delimiters
    so the first `mnemo extract` run can upsert content into them."""
    from mnemo.core.dashboard import BLOCK_BEGIN, BLOCK_END
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    home = (vault / "HOME.md").read_text(encoding="utf-8")
    assert BLOCK_BEGIN in home
    assert BLOCK_END in home
    # Block sits at the top of the file (after frontmatter), before user content
    assert home.find(BLOCK_BEGIN) < home.find("# 🧠 Welcome")


def test_home_template_no_longer_references_vestigial_user_dirs(tmp_path: Path):
    """v0.4.1: HOME.md must not link to shared/people|companies|decisions
    since those aren't scaffolded anymore. They were speculative v0.1 taxonomy
    that stayed empty forever — the tagline 'populates itself' makes
    user-curated-only dirs an anti-pattern."""
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    home = (vault / "HOME.md").read_text(encoding="utf-8")
    assert "[[shared/people]]" not in home
    assert "[[shared/companies]]" not in home
    assert "[[shared/decisions]]" not in home
    assert "You maintain manually" not in home


def test_home_template_no_longer_references_wiki_dirs(tmp_path: Path):
    """v0.4 kills wiki/sources and wiki/compiled — HOME must not mention them."""
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    home = (vault / "HOME.md").read_text(encoding="utf-8")
    assert "wiki/sources" not in home
    assert "wiki/compiled" not in home
    assert "/mnemo promote" not in home
    assert "/mnemo compile" not in home


def test_home_template_mentions_v0_3_1_briefings_and_flags(tmp_path: Path):
    """v0.3.1: HOME.md must describe per-session briefings and the two opt-in
    background flags so users know those features exist and how to turn them on."""
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    home = (vault / "HOME.md").read_text(encoding="utf-8")

    # Briefings are documented as Tier 1 capture
    assert "briefings/sessions/" in home
    # Both opt-in config flags are named so users can grep them
    assert "briefings.enabled" in home
    assert "extraction.auto.enabled" in home
    # Stability field is mentioned so users understand the evolving/stable split
    assert "stability" in home.lower()


def test_readme_template_does_not_promise_no_network_calls(tmp_path: Path):
    """v0.3.1: the optional auto-brain/briefings features invoke `claude --print`,
    which calls Anthropic. README must not claim 'no network calls' anymore —
    that was accurate in v0.1 but became a lie in v0.3. Instead it must
    disclose that the opt-in features send memory/briefing content to Anthropic
    when enabled, and that everything else stays offline."""
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    readme = (vault / "README.md").read_text(encoding="utf-8")

    assert "no network calls" not in readme.lower(), (
        "README must not claim 'no network calls' — the auto-brain and briefing "
        "features call Anthropic's API when enabled"
    )
    # Positive assertions: privacy disclosure names the relevant flags
    assert "extraction.auto.enabled" in readme
    assert "briefings.enabled" in readme
    # Anthropic is named so users understand who receives the data
    assert "Anthropic" in readme


def test_readme_template_does_not_mention_removed_working_dir(tmp_path: Path):
    """v0.3.1: `bots/<agent>/working/` was never created by scaffold and is
    no longer a documented concept. The README previously promised it as
    'your scratch space' — remove that line so users don't look for it."""
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    readme = (vault / "README.md").read_text(encoding="utf-8")
    assert "working/" not in readme, (
        "README must not mention bots/<agent>/working/ — it isn't scaffolded "
        "and no code path creates it"
    )


def test_scaffold_idempotent(tmp_path: Path):
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    (vault / "HOME.md").write_text("# user customized")
    scaffold.scaffold_vault(vault)
    assert (vault / "HOME.md").read_text() == "# user customized"


def test_scaffold_writes_config_with_vault_root(tmp_path: Path):
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    cfg = json.loads((vault / "mnemo.config.json").read_text())
    assert cfg["vaultRoot"] == str(vault)
