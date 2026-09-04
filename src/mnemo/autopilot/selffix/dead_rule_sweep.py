"""Autopilot Tier 1 — Dead rule sweep.

Identifies rules with no usage signal in the last N days and moves them
to ``shared/_archive/``.

Heuristics for "dead":
- 0 hits in ``mcp-access-log.jsonl`` over ``days``
- 0 entries in ``reflex-log.jsonl`` (``emitted`` or ``exported`` arrays) over
  ``days``
- Known to have been created at least ``days`` ago

The age check fails **closed**. It used to read a ``created_at:`` frontmatter
field that no writer in this codebase has ever emitted, so it parsed to
``None`` for every real page and the guard — written as
``if created is not None and created > cutoff`` — never fired. Every rule with
no usage signal was archived regardless of age, including rules minutes old.
Age is now derived from fields that pages actually carry (see
:data:`_AGE_FIELDS`) with an mtime fallback, and a page whose age cannot be
established is never archived.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Set

from mnemo._selfexec import python_argv
from mnemo.autopilot.core import network, pr_budget
from mnemo.autopilot.core.labels import SELF_FIX_LABEL
from mnemo.autopilot.selffix import _gh
from mnemo.autopilot.selffix._perimeter import assert_perimeter
from mnemo.core.log_utils import iter_rotated_rows

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
    # Page frontmatter carries the extraction ``run_id``, which is
    # ``datetime.now().isoformat(timespec="seconds")`` — naive *local* time
    # with no ``Z`` (e.g. ``2026-08-01T20:31:45``). None of the formats above
    # match it. Interpret naive values in the local zone, which is where they
    # were produced; assuming UTC would age them by the UTC offset and push
    # them toward the archive.
    try:
        parsed = datetime.fromisoformat(ts_str)
    except ValueError:
        return None
    return parsed.astimezone() if parsed.tzinfo is None else parsed


#: Frontmatter fields consulted to establish a page's age, best first.
#:
#: ``created_at`` is a true creation stamp and wins if it is ever present (no
#: writer emits it today — see module docstring). ``promoted_at`` /
#: ``extracted_at`` / ``extraction_run`` record the run that last *wrote* the
#: page, so they are refreshed by re-extraction and therefore an upper bound
#: on age: a page can look younger than it is, never older. That is the
#: direction a preservation guard must err in.
_AGE_FIELDS = ("created_at", "promoted_at", "extracted_at", "extraction_run")


def _frontmatter_lines(text: str) -> List[str]:
    """Return the lines of the leading ``---`` frontmatter block, if any.

    Scanning the whole file would let a body line that happens to start with
    ``extracted_at:`` decide the page's age — and a stale date in prose is
    exactly the input that would get a live rule archived.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    out: List[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        out.append(line)
    return []  # unterminated frontmatter — treat as absent


def _rule_age_date(md_path: Path, text: str) -> Optional[datetime]:
    """Best estimate of when *md_path* came into existence.

    Tries the frontmatter fields in :data:`_AGE_FIELDS` order, then falls back
    to filesystem mtime. Returns ``None`` only when age cannot be established
    at all — in which case the caller must **not** archive.
    """
    fm = _frontmatter_lines(text)
    for field in _AGE_FIELDS:
        prefix = f"{field}:"
        for line in fm:
            if line.startswith(prefix):
                parsed = _parse_ts(line.split(":", 1)[1].strip())
                if parsed is not None:
                    return parsed
                break  # field present but unparseable — try the next field
    # Filesystem fallback. Also refreshed by writes, so it too errs young.
    try:
        return datetime.fromtimestamp(md_path.stat().st_mtime, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _active_slugs_from_access_log(vault_root: Path, cutoff: datetime) -> Set[str]:
    """Return set of slugs accessed after *cutoff*.

    Reads the rotated log (``.jsonl.1``) as well as the live one — rotation at
    1MB means the sweep's ``days``-wide window can straddle both files, and
    reading only the live one would silently shrink the window.
    """
    log_path = vault_root / ".mnemo" / "mcp-access-log.jsonl"
    active: Set[str] = set()
    for entry in iter_rotated_rows(log_path):
        ts = _parse_ts(entry.get("ts") or "")
        if ts is None or ts < cutoff:
            continue
        for rule in entry.get("rules") or []:
            slug = rule.get("slug") or ""
            if slug:
                active.add(slug)
    return active


def _active_slugs_from_reflex_log(vault_root: Path, cutoff: datetime) -> Set[str]:
    """Return set of slugs emitted or exported in reflex log after *cutoff*.

    A slug reaching the user only through the repo's exported rules file
    (``entry["exported"]``, recorded on any silence or emission row where
    export suppressed some accepted slugs) is still live — export is a
    delivery path, not an absence of one. Counting only ``emitted`` would
    sweep a rule as dead precisely because export is working (issue #128).

    Reads the rotated log (``.jsonl.1``) as well as the live one — the
    180-day default window easily outlives a single rotation, and reading
    only the live file would silently truncate it (issue #136): a rule whose
    last mention rotated into ``.jsonl.1`` would look dead.
    """
    log_path = vault_root / ".mnemo" / "reflex-log.jsonl"
    active: Set[str] = set()
    for entry in iter_rotated_rows(log_path):
        ts = _parse_ts(entry.get("ts") or "")
        if ts is None or ts < cutoff:
            continue
        for slug in (entry.get("emitted") or []) + (entry.get("exported") or []):
            if slug:
                active.add(slug)
    return active


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
            # Must be old enough to be considered dead. The guard fails
            # CLOSED: a page whose age cannot be established is preserved.
            # Silence about age is not evidence of death.
            created = _rule_age_date(md_path, text)
            if created is None or created > cutoff:
                continue  # unknown age, or too recent — skip
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

    if not network.enabled():
        print(
            f"[autopilot] archived {len(archived)} rule(s) in place; "
            "network off, no PR opened"
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
