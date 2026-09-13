"""Doctor check: a second mnemo install is wired up alongside this one.

A plugin install and a ``mnemo init`` install both register the same four
hooks, and Claude Code fires both. When the two are at different versions the
older copy keeps running code this repo already fixed — silently, because
nothing compares the two. ``mnemo doctor`` reported the vault, never which
install wrote to it.
"""
from __future__ import annotations

from pathlib import Path


def check_duplicate_install(claude_dir: Path | None = None) -> list[str] | None:
    """Return finding lines, or None when this is the only install.

    ``claude_dir`` is injectable for tests; production passes None so it
    resolves to ``~/.claude``.
    """
    try:
        from mnemo import __version__
        from mnemo.install import duplicate_install as dup

        installs = dup.find_duplicate_installs(claude_dir)
        if not installs:
            return None

        findings: list[str] = []
        for install in installs:
            findings.extend(dup.describe(install, __version__))

        if dup.version_skew(installs, __version__):
            findings.append(
                "    → update or remove the plugin: "
                "`claude plugin uninstall mnemo@mnemo-marketplace` "
                "(the direct install already covers this machine), "
                "or `claude plugin update mnemo@mnemo-marketplace` to match."
            )
        return findings or None
    except Exception:
        # Defensive, matching `install_backfill`: a diagnostic must never be
        # the thing that breaks `mnemo doctor`. Config files here are written
        # by Claude Code, not by us, so their shape can change under us.
        return None


def _doctor_check_duplicate_install(
    vault: Path, claude_dir: Path | None = None,
) -> bool:
    """Doctor-registry adapter — True when silent, False on warning.

    ``vault`` is part of every check's signature but unused here: installs are
    machine-scoped (``~/.claude``), not vault-scoped — the same reasoning as
    ``hosts._doctor_check_hosts``.

    ``claude_dir`` is the machine scope, injectable so tests can point at a
    fixture tree. Without it a test can only steer this check by patching
    ``HOME``, which ``os.path.expanduser`` ignores on Windows — it reads
    ``USERPROFILE`` — so such a test silently exercises the developer's real
    home and passes for the wrong reason everywhere except CI.
    """
    findings = check_duplicate_install(claude_dir)
    if not findings:
        return True
    for msg in findings:
        print(f"  ⚠ {msg}" if not msg.startswith("    ") else f"  {msg}")
    return False
