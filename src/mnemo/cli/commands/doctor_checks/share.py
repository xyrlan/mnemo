"""Doctor check: the published rules tree (``mnemo publish``) is not behind the vault.

Stateless on purpose — ``doctor`` runs cold every time and says the same
thing ``mnemo status`` says, as a warning. A once-ever notice went silent
for three days on #229; a check that re-reads the manifest and the vault on
every run cannot.
"""
from __future__ import annotations

from pathlib import Path


def _doctor_check_share_published(vault: Path) -> bool:
    """Warn when the tree in this repo differs from what a publish would write
    now. Silent (and True) when never published, when up to date, or when the
    project cannot be resolved from the cwd — doctor must survive every path."""
    import os

    from mnemo.core.agent import resolve_agent, resolve_canonical_agent
    from mnemo.core.share import format as share_format
    from mnemo.core.share import publish as publish_mod

    try:
        cwd = os.getcwd()
        project = resolve_canonical_agent(cwd).name
        if not project:
            return True
        if publish_mod.read_manifest(vault, project) is None:
            return True
        from mnemo.cli.commands.export import configured_universal_threshold
        repo_root = Path(resolve_agent(cwd).repo_root)
        stale = publish_mod.staleness(vault, project=project, repo_root=repo_root,
                                      universal_threshold=configured_universal_threshold())
    except Exception:  # noqa: BLE001 — a diagnostic must not abort the run
        return True
    if stale is None:
        return True
    total, differing = stale
    if differing == 0:
        return True
    noun = "rule" if total == 1 else "rules"
    print(f"  ⚠ Published rules: {differing} differ from the vault now ({total} {noun} in {share_format.SHARE_DIR})")
    print(f"       → run `mnemo publish`, then commit {share_format.SHARE_DIR}")
    return False
