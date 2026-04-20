"""SessionEnd writes briefings under the canonical agent dir, even from worktrees."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mnemo.hooks import session_end


def _make_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """Create a `proj` main repo + `proj-feature-x` worktree. Return (main, worktree)."""
    main = tmp_path / "proj"
    main.mkdir()
    git_dir = main / ".git"
    git_dir.mkdir()
    wt_gitdir = git_dir / "worktrees" / "feature-x"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "commondir").write_text("../..\n")

    worktree = tmp_path / "proj-feature-x"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n")
    return main, worktree


def test_briefing_path_uses_canonical_agent_from_worktree(tmp_path: Path) -> None:
    """When SessionEnd fires from a worktree, briefing goes under the main repo's agent dir."""
    main, worktree = _make_worktree(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = {"vaultRoot": str(vault), "briefings": {"enabled": True}}

    captured: dict[str, object] = {}

    def fake_spawn(jsonl_path: Path, agent: str) -> None:
        captured["jsonl_path"] = jsonl_path
        captured["agent"] = agent

    with patch.object(session_end, "_spawn_detached_briefing", fake_spawn):
        # Mock the transcript path lookup to return a fake existing path.
        with patch.object(session_end, "_resolve_session_jsonl_path", return_value=tmp_path / "fake.jsonl"):
            (tmp_path / "fake.jsonl").write_text("{}\n")
            session_end._maybe_schedule_briefing(
                cfg,
                vault,
                agent_name="ignored-old-resolution",
                session_id="abc123",
                cwd=str(worktree),
            )

    assert captured["agent"] == "proj", (
        f"expected canonical agent 'proj', got {captured['agent']!r}"
    )
