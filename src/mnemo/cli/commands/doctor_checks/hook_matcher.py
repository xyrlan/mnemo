"""Doctor check: an installed mnemo hook matches fewer tools than this version ships.

The detection moved to :mod:`mnemo.install.hook_drift` when #337 gave it two
more callers — ``mnemo status`` and the session-start repair — that must not
import the doctor package. What is left here is the row itself: how the
finding reads, and which remedy it names.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.install.hook_drift import missing_tools  # re-exported: doctor's own detector

__all__ = ["missing_tools", "check_hook_matcher"]


def check_hook_matcher(
    claude_dir: Path | None = None, cwd: Path | None = None,
) -> list[str] | None:
    """Return finding lines, or None when every installed matcher is current.

    Both scopes ``mnemo init`` writes are read: the global
    ``~/.claude/settings.json`` and the project ``<cwd>/.claude/settings.json``.
    """
    from mnemo.install.hook_drift import scan

    findings: list[str] = []
    for drift in scan(claude_dir, cwd):
        findings.extend(drift.doctor_lines())
        findings.append(
            f"    → run `{drift.remedy}` — rewrites mnemo's hooks and nothing else "
            "(config, statusLine and MCP are left as they are)"
        )
    return findings or None


def _doctor_check_hook_matcher(
    vault: Path, claude_dir: Path | None = None, cwd: Path | None = None,
) -> bool:
    """Doctor-registry adapter — True when silent, False on warning.

    ``vault`` is unused: hooks are wired per machine or per project, not per
    vault. ``claude_dir`` and ``cwd`` are injectable because ``HOME`` is ignored
    by ``expanduser`` on Windows (see ``duplicate_install``).
    """
    try:
        findings = check_hook_matcher(claude_dir, cwd)
    except Exception:
        # A diagnostic must never be what breaks `mnemo doctor`.
        return True
    if not findings:
        return True
    for msg in findings:
        print(f"  ⚠ {msg}" if not msg.startswith("    ") else f"  {msg}")
    return False
