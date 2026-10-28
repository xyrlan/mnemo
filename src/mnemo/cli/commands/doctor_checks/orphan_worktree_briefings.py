"""Doctor check — flag orphan worktree briefing dirs (v0.10).

A briefing dir under ``bots/<X>/briefings/sessions/`` is "orphan" when ``X``
is a worktree's name, ``<canonical>-wt-<suffix>``, AND
``bots/<canonical>/briefings/sessions/`` also exists with at least one
briefing. The combination is the signature of a pre-v0.10 worktree write that
the new canonical-agent code can't see — the SessionStart briefing picker now
resolves every worktree back to its canonical agent, so a directory named
after a worktree will never be read from again unless we surface it and
prompt the user to migrate.

Only the name is there to go on: a briefing records no ``cwd``, and the tree
is usually gone. ``resolve_canonical_agent`` folds a tree into another agent
by its ``.git`` pointer, never by its name, so a sibling checkout that shares
a prefix — ``mnemo``, ``mnemo-src``, ``mnemo-desktop`` — is its own agent and
must never be flagged (#798). The one name that says "worktree of ``<repo>``"
is the ``-wt-`` suffix that ``dispatch.worktree_path`` and the hand-made
``<repo>-wt-<name>`` trees carry, so that is the only thing folded.

Returning ``None`` (silent) on any error is deliberate: the doctor check must
never be the thing that breaks ``mnemo doctor``. When in doubt, say nothing.
"""
from __future__ import annotations

from pathlib import Path


def worktree_canonical(name: str) -> str | None:
    """The agent a ``<repo>-wt-<suffix>`` dir was cut from, else ``None``.

    Split on the *first* ``-wt-``: a child dispatched from inside a child's
    tree is ``mnemo-desktop-wt-round6-wt-41``, still ``mnemo-desktop``'s.
    """
    from mnemo.core.dispatch import WORKTREE_SUFFIX

    base, sep, suffix = name.partition(WORKTREE_SUFFIX)
    if not sep or not base or not suffix:
        return None
    return base


def check_orphan_worktree_briefings(vault_root: Path) -> list[str] | None:
    """Return human-readable findings, or ``None`` when there's nothing to flag.

    Heuristic:
      1. Enumerate ``bots/*/briefings/sessions/`` dirs that hold >=1 briefing.
      2. For each such dir named ``<canonical>-wt-<suffix>``, check whether
         ``<canonical>`` is also in the set from (1).
      3. If yes, the directory is a probable worktree orphan — emit a finding.

    A first-``-`` split is what this did until #798, and it named
    ``mnemo-src`` and ``mnemo-desktop`` orphans of ``mnemo``: three checkouts
    that canonical resolution keeps apart. The "canonical must itself have
    >=1 briefing" guard could not catch it, because all three are active.
    """
    try:
        bots_root = vault_root / "bots"
        if not bots_root.is_dir():
            return None

        # Build the set of agents that currently own briefings on disk.
        canonicals_with_briefings: set[str] = set()
        for agent_dir in bots_root.iterdir():
            if not agent_dir.is_dir():
                continue
            sessions = agent_dir / "briefings" / "sessions"
            if sessions.is_dir() and any(sessions.glob("*.md")):
                canonicals_with_briefings.add(agent_dir.name)

        findings: list[str] = []
        for agent_dir in sorted(bots_root.iterdir()):
            if not agent_dir.is_dir():
                continue
            name = agent_dir.name
            prefix = worktree_canonical(name)
            if prefix is None or prefix not in canonicals_with_briefings:
                continue
            sessions = agent_dir / "briefings" / "sessions"
            if not sessions.is_dir():
                continue
            orphans = list(sessions.glob("*.md"))
            if not orphans:
                continue
            findings.append(
                f"orphan worktree briefings: bots/{name}/briefings/sessions/ has "
                f"{len(orphans)} file(s); canonical agent {prefix!r} also has briefings. "
                f"Run: mnemo migrate-worktree-briefings --repos <repo_path> --dry-run"
            )

        return findings or None
    except OSError:
        # Defensive: surface nothing rather than break `mnemo doctor`.
        return None


def _doctor_check_orphan_worktree_briefings(vault: Path) -> bool:
    """Doctor-registry adapter — returns True when silent, False on warning.

    Emits one ``⚠`` line per orphan directory and a single ``→`` remediation
    footer pointing at ``mnemo migrate-worktree-briefings``.
    """
    findings = check_orphan_worktree_briefings(vault)
    if not findings:
        return True
    for msg in findings:
        print(f"  \u26a0 {msg}")
    return False
