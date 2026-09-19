"""Doctor check: procedures children keep rediscovering, absent from CLAUDE.md (#392).

The row exists because the command would otherwise be a pull mechanism, and
#385 counted what happens to those: ``Skill`` was used by 5 of 182 children.
``mnemo procedures`` is run by someone who already knows it exists; ``doctor``
is run when something feels wrong, which is the same day a child gets a repo's
setup wrong.

Stateless like every other row: it re-reads the transcripts each run, and the
vault's decision ledger is what keeps a dropped candidate quiet (#380's rule).
"""
from __future__ import annotations

from pathlib import Path


def _doctor_check_rediscovered_procedures(vault: Path) -> bool:
    """Warn when two children of this repo worked out something the file omits.

    Silent (and True) when the directory resolves to no project, when no
    candidate clears the bar, or on any error — a diagnostic must survive
    every path, including a machine with no transcripts at all.
    """
    import os

    try:
        from mnemo.core import config as cfg_mod
        from mnemo.core import procedures as P
        from mnemo.core.agent import resolve_canonical_agent

        repo = resolve_canonical_agent(os.getcwd()).name
        if not repo:
            return True
        cfg = cfg_mod.load_config().get("procedures", {})
        candidates = [
            c for c in P.scan(
                os.path.expanduser("~/.claude/projects"),
                min_children=int(cfg.get("minChildren", 2)),
                max_shape_repos=int(cfg.get("maxShapeRepos", 2)),
                repo=repo,
                ledger_rows=P.ledger_rows(vault),
            )
            if not c.stated
        ]
    except Exception:  # noqa: BLE001 — a diagnostic must not abort the run
        return True

    if not candidates:
        return True
    count = len(candidates)
    noun = "procedure" if count == 1 else "procedures"
    print(f"  ⚠ Rediscovered procedures: {count} {noun} two or more children of "
          f"{repo} worked out for themselves are not in its CLAUDE.md")
    for candidate in candidates[:3]:
        print(f"       • {candidate.key}: {candidate.command[:70]}")
    print("       → `mnemo procedures` proposes the line; nothing is written until "
          "you accept it")
    return False
