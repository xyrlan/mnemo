"""Doctor check surfaces orphan worktree briefings + suggests the migration."""
from __future__ import annotations

from pathlib import Path

from mnemo.cli.commands.doctor_checks import orphan_worktree_briefings as check_mod


def _seed_briefing(vault: Path, agent: str, session_id: str) -> None:
    sessions = vault / "bots" / agent / "briefings" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.md").write_text("briefing", encoding="utf-8")


def _seed_orphan(vault: Path, canonical: str, worktree_name: str, session_id: str) -> None:
    _seed_briefing(vault, worktree_name, session_id)
    # Also seed at least one canonical briefing so the heuristic fires.
    _seed_briefing(vault, canonical, "canonical")


def test_check_finds_orphan_dirs(tmp_vault: Path) -> None:
    _seed_orphan(tmp_vault, "myproj", "myproj-wt-feature-x", "wt-session")
    findings = check_mod.check_orphan_worktree_briefings(tmp_vault)
    assert findings is not None
    assert any("myproj-wt-feature-x" in f for f in findings)
    assert any("migrate-worktree-briefings" in f for f in findings)


def test_check_silent_when_no_orphans(tmp_vault: Path) -> None:
    findings = check_mod.check_orphan_worktree_briefings(tmp_vault)
    assert findings is None or findings == []


def test_check_silent_when_canonical_has_no_briefings(tmp_vault: Path) -> None:
    """If the canonical agent has zero briefings, we can't be sure the
    `<canonical>-<suffix>` dir is actually a worktree leftover (could be
    a totally separate project that happens to share a name prefix)."""
    sessions = tmp_vault / "bots" / "myproj-wt-feature-x" / "briefings" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "wt.md").write_text("x", encoding="utf-8")
    findings = check_mod.check_orphan_worktree_briefings(tmp_vault)
    assert findings is None or findings == []


def test_sibling_checkouts_sharing_a_prefix_are_never_flagged(tmp_vault: Path) -> None:
    """#798: ``alpha``, ``alpha-src`` and ``alpha-desk`` are three checkouts,
    each its own canonical agent. Every one of them holding briefings is the
    normal state of a vault, not three orphans of ``alpha``."""
    for agent in ("alpha", "alpha-src", "alpha-desk"):
        _seed_briefing(tmp_vault, agent, "s1")
    findings = check_mod.check_orphan_worktree_briefings(tmp_vault)
    assert findings is None or findings == []


def test_worktree_of_a_hyphenated_repo_names_that_repo(tmp_vault: Path) -> None:
    """A dispatch child of a child of ``alpha-desk`` is ``alpha-desk``'s
    orphan, not ``alpha``'s; a hand-made tree of a hyphenated repo is found
    at all, where a first-``-`` split looked for a ``beta`` agent."""
    for agent in ("alpha", "alpha-desk", "beta-shop"):
        _seed_briefing(tmp_vault, agent, "s1")
    _seed_briefing(tmp_vault, "alpha-desk-wt-round2-wt-41", "child")
    _seed_briefing(tmp_vault, "alpha-wt-c-parser", "piece")
    _seed_briefing(tmp_vault, "beta-shop-wt-workspace-1", "hand")
    findings = check_mod.check_orphan_worktree_briefings(tmp_vault) or []
    assert len(findings) == 3
    by_dir = {f.split("bots/", 1)[1].split("/", 1)[0]: f for f in findings}
    assert "canonical agent 'alpha-desk'" in by_dir["alpha-desk-wt-round2-wt-41"]
    assert "canonical agent 'alpha'" in by_dir["alpha-wt-c-parser"]
    assert "canonical agent 'beta-shop'" in by_dir["beta-shop-wt-workspace-1"]


def test_worktree_canonical_reads_only_the_wt_suffix() -> None:
    assert check_mod.worktree_canonical("alpha-wt-12") == "alpha"
    assert check_mod.worktree_canonical("alpha-desk-wt-r6-wt-41") == "alpha-desk"
    assert check_mod.worktree_canonical("alpha-src") is None
    assert check_mod.worktree_canonical("alpha-12-x7k2q") is None
    assert check_mod.worktree_canonical("alpha-wt-") is None
    assert check_mod.worktree_canonical("-wt-12") is None
