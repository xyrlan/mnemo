"""Doctor check: live rules citing a file that is no longer in this repo (#274).

Stateless like every other row — it re-reads the vault and ``git ls-tree`` on
each run, so a rule that goes stale after a rename is reported the next time
doctor runs, with no notice state to go quiet on (#229).
"""
from __future__ import annotations

from pathlib import Path


def _doctor_check_stale_citations(vault: Path) -> bool:
    """Warn when live rules for this repo cite a path absent at HEAD.

    Silent (and True) outside a git repo, when no rules are attributed to the
    project, or when every citation resolves — doctor must survive every path.
    """
    import os

    from mnemo.core import stale as stale_mod
    from mnemo.core.agent import resolve_canonical_agent

    try:
        agent = resolve_canonical_agent(os.getcwd())
        if not agent.name or not agent.has_git:
            return True
        report = stale_mod.run(vault, project=agent.name, repo_root=agent.repo_root)
    except Exception:  # noqa: BLE001 — a diagnostic must not abort the run
        return True

    count = len(report.stale_pages)
    if not count:
        return True
    noun = "rule" if count == 1 else "rules"
    print(
        f"  ⚠ Stale citations: {count} live {noun} cite a file not in "
        f"{agent.name} at HEAD ({report.pages_scanned} scanned)"
    )
    print("       → run `mnemo stale` to see which path, and where it moved to")
    return False
