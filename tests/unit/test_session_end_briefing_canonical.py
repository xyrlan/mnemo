"""SessionEnd files the briefing under the name ``main()`` resolved for the session.

#225 made that name canonical (worktree -> main repo). #247: the briefing
helper was still re-resolving from the cwd on its own, and a dispatched tree
is routinely removed before its child is stopped, so the second resolution
found no ``.git`` above the path and fell back to the directory basename —
one orphan ``bots/<repo>-wt-N/briefings/`` per stopped child. The storage
agent is now the name ``main()`` passes in (session cache first, canonical
resolution on a miss); the helper does not look at the tree.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

from mnemo.core import agent
from mnemo.hooks import session_end


def _make_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """Create a `proj` main repo + `proj-feature-x` worktree. Return (main, worktree)."""
    main = tmp_path / "proj"
    main.mkdir()
    git_dir = main / ".git"
    git_dir.mkdir()
    wt_gitdir = git_dir / "worktrees" / "feature-x"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "commondir").write_text("../..\n", encoding="utf-8")

    worktree = tmp_path / "proj-feature-x"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")
    return main, worktree


def _schedule(tmp_path: Path, *, agent_name: str, cwd: Path) -> str:
    """Run the helper with briefings on and a transcript present; return the spawned agent."""
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = {"vaultRoot": str(vault), "briefings": {"enabled": True}}
    transcript = tmp_path / "fake.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    captured: dict[str, str] = {}
    with patch.object(
        session_end, "_spawn_detached_briefing",
        lambda _jsonl, name: captured.update(agent=name),
    ), patch.object(
        session_end, "_resolve_session_jsonl_path", return_value=transcript
    ):
        session_end._maybe_schedule_briefing(
            cfg, vault, agent_name=agent_name, session_id="abc123", cwd=str(cwd),
        )
    return captured["agent"]


def test_briefing_uses_the_name_main_resolved_while_the_tree_is_alive(tmp_path: Path) -> None:
    _main, worktree = _make_worktree(tmp_path)
    assert _schedule(tmp_path, agent_name="proj", cwd=worktree) == "proj"


def test_briefing_uses_the_name_main_resolved_after_the_tree_is_removed(tmp_path: Path) -> None:
    """#247: the tree is gone by the time SessionEnd fires; the cached name still holds."""
    _main, worktree = _make_worktree(tmp_path)
    shutil.rmtree(worktree)  # what `git worktree remove --force` leaves: nothing
    assert _schedule(tmp_path, agent_name="proj", cwd=worktree) == "proj"


def test_resolving_from_the_dead_cwd_is_what_made_the_orphan(tmp_path: Path) -> None:
    """Counter-test: documents why the helper must not resolve on its own."""
    _main, worktree = _make_worktree(tmp_path)
    assert agent.resolve_canonical_agent(str(worktree)).name == "proj"
    shutil.rmtree(worktree)
    assert agent.resolve_canonical_agent(str(worktree)).name == "proj-feature-x"
