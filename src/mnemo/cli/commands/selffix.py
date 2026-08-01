"""``mnemo autopilot self-fix {doctor,sweep,telemetry} [--dry-run]``

Tier 1 self-fix commands: detect and optionally open PRs for doctor
warnings, dead rules, and telemetry anomalies.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional


def _vault() -> Path:
    from mnemo import cli
    return cli._resolve_vault()


def _repo_root(vault: Path) -> Optional[Path]:
    """Return the repo that holds *vault*, or ``None`` if it has none.

    Anchoring on the vault matters: self-fix edits vault files, so the cwd's
    repo — which is whatever the autopilot happened to be launched from — is
    the wrong place to cut a branch, and branching it corrupts an unrelated
    checkout while producing a PR with no diff.
    """
    from mnemo.autopilot.selffix import _gh
    return _gh.resolve_repo_root(vault)


def cmd_selffix(args: argparse.Namespace) -> int:
    """Dispatch ``autopilot self-fix`` subcommands."""
    action = getattr(args, "selffix_action", None)
    dry_run = getattr(args, "dry_run", False)

    vault = _vault()
    repo = _repo_root(vault)

    if action == "doctor":
        return _do_doctor(vault, repo, dry_run=dry_run)
    if action == "sweep":
        return _do_sweep(vault, repo, dry_run=dry_run)
    if action == "telemetry":
        return _do_telemetry(vault, repo, dry_run=dry_run)

    if action is None and dry_run:
        # Global --dry-run: run all three
        rc = 0
        rc |= _do_doctor(vault, repo, dry_run=True)
        rc |= _do_sweep(vault, repo, dry_run=True)
        rc |= _do_telemetry(vault, repo, dry_run=True)
        return rc

    print("usage: mnemo autopilot self-fix {doctor,sweep,telemetry} [--dry-run]")
    return 2


def _do_doctor(vault: Path, repo: Optional[Path], *, dry_run: bool) -> int:
    from mnemo.autopilot.selffix.doctor_fixer import detect_fixable, open_doctor_fix_pr

    warnings = detect_fixable(vault_root=vault)
    if not warnings:
        print("[autopilot] doctor: no auto-fixable warnings found")
        return 0

    print(f"[autopilot] doctor: {len(warnings)} auto-fixable warning(s):")
    for w in warnings:
        print(f"  • {w.rule_path.name}: {w.kind} ({w.detail})")

    if dry_run:
        print("[autopilot] dry-run: no PR opened")
        return 0

    open_doctor_fix_pr(warnings, vault_root=vault, repo_root=repo)
    return 0


def _do_sweep(vault: Path, repo: Optional[Path], *, dry_run: bool) -> int:
    from mnemo.autopilot.selffix.dead_rule_sweep import detect_dead_rules, open_dead_rule_pr

    dead = detect_dead_rules(vault_root=vault)
    if not dead:
        print("[autopilot] sweep: no dead rules found")
        return 0

    print(f"[autopilot] sweep: {len(dead)} dead rule(s):")
    for r in dead:
        print(f"  • {r.slug} (inactive >{r.last_seen_days}d)")

    if dry_run:
        print("[autopilot] dry-run: no PR opened")
        return 0

    open_dead_rule_pr(dead, vault_root=vault, repo_root=repo)
    return 0


def _do_telemetry(vault: Path, repo: Optional[Path], *, dry_run: bool) -> int:
    from mnemo.autopilot.selffix.telemetry_doctor import scan_telemetry, open_telemetry_fix_pr

    anomalies = scan_telemetry(vault_root=vault)
    if not anomalies:
        print("[autopilot] telemetry: no anomalies found")
        return 0

    print(f"[autopilot] telemetry: {len(anomalies)} anomaly(ies):")
    for a in anomalies:
        print(f"  • {a.kind}: {a.detail}")

    if dry_run:
        print("[autopilot] dry-run: no PR opened")
        return 0

    open_telemetry_fix_pr(anomalies, vault_root=vault, repo_root=repo)
    return 0
