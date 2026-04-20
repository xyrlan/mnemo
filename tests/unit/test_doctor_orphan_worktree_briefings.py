"""Doctor check surfaces orphan worktree briefings + suggests the migration."""
from __future__ import annotations

from pathlib import Path

from mnemo.cli.commands.doctor_checks import orphan_worktree_briefings as check_mod


def _seed_orphan(vault: Path, canonical: str, worktree_name: str, session_id: str) -> None:
    sessions = vault / "bots" / worktree_name / "briefings" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / f"{session_id}.md").write_text("orphan", encoding="utf-8")
    # Also seed at least one canonical briefing so the heuristic fires.
    (vault / "bots" / canonical / "briefings" / "sessions").mkdir(parents=True, exist_ok=True)
    (vault / "bots" / canonical / "briefings" / "sessions" / "canonical.md").write_text(
        "main", encoding="utf-8",
    )


def test_check_finds_orphan_dirs(tmp_vault: Path) -> None:
    _seed_orphan(tmp_vault, "myproj", "myproj-feature-x", "wt-session")
    findings = check_mod.check_orphan_worktree_briefings(tmp_vault)
    assert findings is not None
    assert any("myproj-feature-x" in f for f in findings)
    assert any("migrate-worktree-briefings" in f for f in findings)


def test_check_silent_when_no_orphans(tmp_vault: Path) -> None:
    findings = check_mod.check_orphan_worktree_briefings(tmp_vault)
    assert findings is None or findings == []


def test_check_silent_when_canonical_has_no_briefings(tmp_vault: Path) -> None:
    """If the canonical agent has zero briefings, we can't be sure the
    `<canonical>-<suffix>` dir is actually a worktree leftover (could be
    a totally separate project that happens to share a name prefix)."""
    sessions = tmp_vault / "bots" / "myproj-feature-x" / "briefings" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "wt.md").write_text("x", encoding="utf-8")
    findings = check_mod.check_orphan_worktree_briefings(tmp_vault)
    assert findings is None or findings == []
