"""``mnemo export`` — the project's learned rules as a file another tool loads.

See docs/superpowers/specs/2026-09-02-distribution-design.md § 1. The CLI
in :mod:`mnemo.cli.commands.export` only prints; every decision is here so
``init --host`` (PR F) and ``status`` can reuse it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from mnemo.core.export import manifest as manifest_mod
from mnemo.core.export.render import TOKEN_WARN, entry_hash, estimated_tokens, render_block
from mnemo.core.export.select import DEFAULT_TYPES, ExportRule, select_rules
from mnemo.core.export.writers import (  # noqa: F401  — re-exported for callers
    MarkerError, Target, TargetError, remove_target, target_for, write_target,
)

ALL_TYPES: tuple = ("feedback", "user", "reference", "project")


@dataclass
class ExportReport:
    project: str
    target: Target
    rules: List[ExportRule] = field(default_factory=list)
    block: str = ""
    tokens: int = 0
    wrote: bool = False
    removed: bool = False
    warning: Optional[str] = None

    @property
    def universal(self) -> int:
        return sum(1 for r in self.rules if r.universal)


def current_hashes(vault_root: Path, *, project: str, types: Sequence[str] = DEFAULT_TYPES,
                   universal_threshold: int = 2) -> Dict[str, str]:
    """slug → entry hash for what an export would write right now (status uses this)."""
    return {r.slug: entry_hash(r) for r in select_rules(
        vault_root, project=project, types=types, universal_threshold=universal_threshold)}


def run_export(
    vault_root: Path,
    *,
    project: str,
    repo_root: Path,
    host: str = "claude",
    target: str = "auto",
    types: Sequence[str] = DEFAULT_TYPES,
    universal_threshold: int = 2,
    limit: Optional[int] = None,
    dry_run: bool = False,
    remove: bool = False,
    force_warning: bool = False,
    today: Optional[str] = None,
) -> ExportReport:
    """Select, render, write (or remove). Raises TargetError / MarkerError."""
    vault_root = Path(vault_root)
    repo_root = Path(repo_root)
    tgt = target_for(host, target, repo_root)
    report = ExportReport(project=project, target=tgt)

    if remove:
        report.removed = remove_target(tgt)
        manifest_mod.delete_manifest(vault_root, project)
        return report

    report.rules = select_rules(vault_root, project=project, types=types,
                                universal_threshold=universal_threshold, limit=limit)
    if not report.rules:
        return report
    report.block = render_block(report.rules, project=project,
                                today=today or date.today().isoformat())
    report.tokens = estimated_tokens(report.block)
    if force_warning or report.tokens > TOKEN_WARN:
        report.warning = (
            f"about {report.tokens} tokens will load on every prompt — "
            "consider --limit N to keep the most-sourced rules only"
        )
    if dry_run:
        return report

    write_target(tgt, report.block)
    manifest_mod.write_manifest(
        vault_root, project, host=host, target=tgt.name, cwd=str(repo_root.resolve()),
        path=tgt.path.relative_to(repo_root).as_posix(),
        rules={r.slug: entry_hash(r) for r in report.rules},
    )
    report.wrote = True
    return report
