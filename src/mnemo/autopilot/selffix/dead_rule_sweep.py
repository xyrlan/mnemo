"""Autopilot Tier 1 — Dead rule sweep.

Identifies rules with no usage signal in the last N days and moves them
to ``shared/_archive/``.

Heuristics for "dead":
- 0 hits in ``mcp-access-log.jsonl`` over ``days``
- 0 entries in ``reflex-log.jsonl`` (``emitted`` arrays) over ``days``
- Created at least ``days`` ago
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Set

from mnemo._selfexec import python_argv
from mnemo.autopilot.core import pr_budget
from mnemo.autopilot.core.labels import SELF_FIX_LABEL
from mnemo.autopilot.selffix import _gh
from mnemo.autopilot.selffix._perimeter import assert_perimeter

_SHARED_SUBTYPES = ("feedback", "user", "reference")

#: Default window (days) before a rule is considered dead. Bumped from 90 →
#: 180 so prior schema bumps that broke ``mcp-access-log`` slug continuity
#: don't sweep otherwise-active rules.
DEFAULT_DEAD_WINDOW_DAYS = 180

#: Maximum rules archived in a single sweep PR. Caps blast radius if the
#: detector ever over-fires (e.g., after a schema bump invalidates logs).
MAX_RULES_PER_SWEEP_PR = 50


@dataclass
class DeadRule:
    """A rule with no usage signal over the configured window."""

    rule_path: Path
    slug: str
    last_seen_days: int  # days since last activity (>= days threshold means dead)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts_str: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _active_slugs_from_access_log(vault_root: Path, cutoff: datetime) -> Set[str]:
    """Return set of slugs accessed after *cutoff*."""
    log_path = vault_root / ".mnemo" / "mcp-access-log.jsonl"
    if not log_path.exists():
        return set()
    active: Set[str] = set()
    try:
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(entry.get("ts") or "")
            if ts is None or ts < cutoff:
                continue
            for rule in entry.get("rules") or []:
                slug = rule.get("slug") or ""
                if slug:
                    active.add(slug)
    except OSError:
        pass
    return active


def _active_slugs_from_reflex_log(vault_root: Path, cutoff: datetime) -> Set[str]:
    """Return set of slugs emitted in reflex log after *cutoff*."""
    log_path = vault_root / ".mnemo" / "reflex-log.jsonl"
    if not log_path.exists():
        return set()
    active: Set[str] = set()
    try:
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(entry.get("ts") or "")
            if ts is None or ts < cutoff:
                continue
            for slug in entry.get("emitted") or []:
                if slug:
                    active.add(slug)
    except OSError:
        pass
    return active


def _rule_created_at(text: str) -> Optional[datetime]:
    """Parse ``created_at:`` from frontmatter."""
    for line in text.splitlines():
        if line.startswith("created_at:"):
            val = line.split(":", 1)[1].strip()
            return _parse_ts(val)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_dead_rules(
    *,
    vault_root: Path,
    days: int = DEFAULT_DEAD_WINDOW_DAYS,
) -> List[DeadRule]:
    """Return rules in ``shared/`` that have had no usage signal in *days* days."""
    shared = vault_root / "shared"
    if not shared.is_dir():
        return []

    cutoff = _now() - timedelta(days=days)
    active_access = _active_slugs_from_access_log(vault_root, cutoff)
    active_reflex = _active_slugs_from_reflex_log(vault_root, cutoff)
    all_active = active_access | active_reflex

    dead: List[DeadRule] = []
    for subtype in _SHARED_SUBTYPES:
        type_dir = shared / subtype
        if not type_dir.is_dir():
            continue
        for md_path in sorted(type_dir.glob("*.md")):
            slug = md_path.stem
            if slug in all_active:
                continue
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Must be old enough to be considered dead
            created = _rule_created_at(text)
            if created is not None and created > cutoff:
                continue  # too recent — skip
            dead.append(DeadRule(rule_path=md_path, slug=slug, last_seen_days=days))

    return dead


def archive_rule(rule_path: Path, *, vault_root: Path) -> Path:
    """Move *rule_path* to ``shared/_archive/``.

    Returns the new path. The parent archive dir is created if needed.
    """
    archive_dir = vault_root / "shared" / "_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / rule_path.name
    rule_path.rename(dest)
    return dest


# ---------------------------------------------------------------------------
# pytest helper (mockable)
# ---------------------------------------------------------------------------


def _run_pytest(*, repo_root: Path) -> bool:
    """Run pytest in *repo_root*.  Returns True iff exit code is 0 or 5.

    Exit code 5 ("no tests collected") is treated as success so the
    autopilot can sweep dead rules from a vault dir with no test suite.

    Returns False when no Python interpreter can be found. Under a frozen
    build ``sys.executable`` is the mnemo binary, so it cannot be used to run
    pytest — and failing closed here is right: this is a safety gate, and an
    unverifiable sweep must not be treated as a verified one.
    """
    argv = python_argv("-m", "pytest", "-q", "--tb=short")
    if argv is None:
        return False
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode in (0, 5)


# ---------------------------------------------------------------------------
# PR opening
# ---------------------------------------------------------------------------


def open_dead_rule_pr(
    rules: List[DeadRule],
    *,
    vault_root: Path,
    repo_root: Optional[Path],
    dry_run: bool = False,
) -> Optional[int]:
    """Archive *rules* and open a self-fix PR.

    Rules are archived in the live vault either way; *repo_root* only controls
    whether the move can also become a PR. Pass ``None`` when the vault is not
    under version control.

    Returns the PR number on success, ``None`` otherwise.
    """
    if not rules:
        return None

    if len(rules) > MAX_RULES_PER_SWEEP_PR:
        print(
            f"[autopilot] sweep capped: {len(rules)} dead rules detected, "
            f"archiving first {MAX_RULES_PER_SWEEP_PR} (cap={MAX_RULES_PER_SWEEP_PR})"
        )
        rules = rules[:MAX_RULES_PER_SWEEP_PR]

    ok, reason = pr_budget.can_open(vault_root=vault_root, category="dead_rule_sweep")
    if not ok:
        print(f"[autopilot] dead-rule sweep skipped: {reason}")
        return None

    # Archive rules. Both ends of the move are recorded: the commit has to
    # carry the deletion of the old path, not just the new copy.
    archived: List[Path] = []
    moved: List[Path] = []
    for r in rules:
        if not r.rule_path.exists():
            continue
        try:
            dest = archive_rule(r.rule_path, vault_root=vault_root)
            archived.append(dest)
            moved += [r.rule_path, dest]
        except Exception as exc:
            print(f"[autopilot] failed to archive {r.rule_path.name}: {exc}")

    if not archived:
        return None

    # Perimeter guard
    try:
        assert_perimeter(
            archived, repo_root=repo_root or vault_root, vault_root=vault_root
        )
    except Exception as exc:
        print(f"[autopilot] perimeter violation, aborting sweep PR: {exc}")
        return None

    if dry_run:
        print(f"[autopilot] dry-run: would open dead-rule PR for {len(archived)} rule(s)")
        for p in archived:
            print(f"  • {p}")
        return None

    if repo_root is None:
        print(
            f"[autopilot] archived {len(archived)} rule(s); "
            "vault is not a git repo, no PR opened"
        )
        return None

    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    branch = f"mnemo/self-fix/sweep-{date_tag}"
    title = f"fix(autopilot): dead-rule sweep {date_tag}"

    worktree = _gh.create_worktree(branch, repo_root=repo_root)
    if worktree is None:
        print("[autopilot] sweep skipped: could not create worktree")
        return None

    try:
        _gh.mirror_paths(moved, source_root=repo_root, worktree=worktree)
        if not _gh.commit_all(title, worktree=worktree):
            print("[autopilot] sweep skipped: nothing to commit")
            return None

        if not _run_pytest(repo_root=worktree):
            print("[autopilot] sweep aborted: pytest failed after archiving rules")
            return None

        if not _gh.push_branch(branch, repo_root=worktree):
            print("[autopilot] sweep aborted: could not push branch")
            return None

        body_lines = [
            f"Archiving {len(rules)} rule(s) with no usage signal in "
            f"{DEFAULT_DEAD_WINDOW_DAYS} days:\n"
        ]
        for r in rules:
            body_lines.append(f"- `{r.slug}` (last seen: >{r.last_seen_days}d ago)")
        body = "\n".join(body_lines)

        pr_number = _gh.open_pr(
            branch=branch,
            title=title,
            body=body,
            labels=[SELF_FIX_LABEL],
            draft=True,
            repo_root=worktree,
        )
    finally:
        _gh.remove_worktree(worktree, repo_root=repo_root)

    if pr_number is not None:
        pr_budget.record_opened(
            vault_root=vault_root, category="dead_rule_sweep", pr_number=pr_number
        )
        print(f"[autopilot] opened dead-rule sweep PR #{pr_number}")
    return pr_number
