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

ALL_TYPES: tuple[str, ...] = ("feedback", "user", "reference", "project")


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
    user_pages: List[str] = field(default_factory=list)
    full: bool = False

    @property
    def universal(self) -> int:
        return sum(1 for r in self.rules if r.universal)


def current_hashes(vault_root: Path, *, project: str, types: Sequence[str] = DEFAULT_TYPES,
                   universal_threshold: int = 2, full: bool = False) -> Dict[str, str]:
    """slug → entry hash for what an export would write right now (status uses this).

    *full* must match the format of the export being compared against — the
    manifest records it — since the two renderings hash differently.
    """
    return {r.slug: entry_hash(r, full=full) for r in select_rules(
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
    full: bool = False,
) -> ExportReport:
    """Select, render, write (or remove). Raises TargetError / MarkerError.

    *full* writes each rule's whole body; the default is the compact format
    (heading, first paragraph, quote — see :mod:`render`).
    """
    vault_root = Path(vault_root)
    repo_root = Path(repo_root).resolve()
    tgt = target_for(host, target, repo_root)
    report = ExportReport(project=project, target=tgt, full=full)

    if remove:
        report.removed = remove_target(tgt)
        manifest_mod.delete_manifest(vault_root, project)
        return report

    report.rules = select_rules(vault_root, project=project, types=types,
                                universal_threshold=universal_threshold, limit=limit)
    if not report.rules:
        return report
    report.user_pages = [r.slug for r in report.rules if r.page_type == "user"]
    report.block = render_block(report.rules, project=project,
                                today=today or date.today().isoformat(), full=full)
    report.tokens = estimated_tokens(report.block)
    n = len(report.rules)
    suggested_limit = max(1, n // 2)
    if force_warning:
        report.warning = (
            f"includes reference/project pages, which are noisier than corrections: "
            f"{n} rules, about {report.tokens} tokens on every prompt — "
            f"try --limit {suggested_limit}"
        )
    elif report.tokens > TOKEN_WARN:
        # In full mode the biggest lever is the format itself: the compact
        # block is under half the size on real projects, so name it first.
        remedy = (
            f"try without --full, or --limit {suggested_limit}" if full
            else f"try --limit {suggested_limit} to keep the most-sourced ones"
        )
        report.warning = (
            f"about {report.tokens} tokens from {n} rules will load on every prompt — {remedy}"
        )
    if dry_run:
        return report

    write_target(tgt, report.block)
    manifest_mod.write_manifest(
        vault_root, project, host=host, target=tgt.name, cwd=str(repo_root),
        path=tgt.path.relative_to(repo_root).as_posix(),
        rules={r.slug: entry_hash(r, full=full) for r in report.rules},
        format="full" if full else "compact",
    )
    report.wrote = True
    return report
