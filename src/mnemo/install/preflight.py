"""Pre-install environment validation."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Severity = Literal["error", "warning", "info"]


@dataclass
class Issue:
    kind: str
    severity: Severity
    message: str
    remediation: str


@dataclass
class PreflightResult:
    ok: bool
    issues: list[Issue] = field(default_factory=list)


def _python_ok() -> bool:
    return sys.version_info >= (3, 8)


def _vault_writable(vault_root: Path) -> bool:
    parent = vault_root.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / ".mnemo-write-test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def _settings_writable(settings: Path | None = None) -> bool:
    target = Path(settings) if settings is not None else Path(os.path.expanduser("~/.claude/settings.json"))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return os.access(target, os.W_OK)
        return os.access(target.parent, os.W_OK)
    except OSError:
        return False


def _disk_space_ok(vault_root: Path, min_bytes: int = 10 * 1024 * 1024) -> bool:
    try:
        target = vault_root if vault_root.exists() else vault_root.parent
        free = shutil.disk_usage(target).free
        return free >= min_bytes
    except OSError:
        return True  # don't block on probe failure


def run_preflight(
    vault_root: Path | None = None,
    *,
    settings_target: Path | None = None,
) -> PreflightResult:
    vault_root = Path(vault_root) if vault_root else Path(os.path.expanduser("~/mnemo"))
    issues: list[Issue] = []

    if not _python_ok():
        issues.append(Issue(
            "python_version", "error",
            f"Python 3.8+ required (have {sys.version_info.major}.{sys.version_info.minor})",
            "Upgrade Python: https://www.python.org/downloads/",
        ))
    if not _vault_writable(vault_root):
        issues.append(Issue(
            "vault_unwritable", "error",
            f"Cannot write to {vault_root.parent}",
            f"Pick a different vault location with --vault-root, or run: chmod u+w {vault_root.parent}",
        ))
    if not _settings_writable(settings_target):
        target_label = str(settings_target) if settings_target is not None else "~/.claude/settings.json"
        issues.append(Issue(
            "settings_unwritable", "error",
            f"Cannot write to {target_label}",
            f"Run: chmod u+w {target_label}",
        ))
    if not _disk_space_ok(vault_root):
        issues.append(Issue(
            "disk_space", "error",
            "Less than 10MB free disk space at vault location",
            "Free up disk space or pick a different --vault-root",
        ))
    if shutil.which("rsync") is None:
        issues.append(Issue(
            "rsync_missing", "warning",
            "rsync not found in PATH — using slower pure-Python fallback",
            "Install rsync (apt install rsync / brew install rsync) for faster mirror",
        ))

    ok = not any(i.severity == "error" for i in issues)
    return PreflightResult(ok=ok, issues=issues)
