"""Autopilot Tier 1 — Doctor self-fix.

Detects auto-fixable doctor warnings and can open a self-fix PR.

Auto-fixable categories:
- ``source_path_missing`` — strip the source line from frontmatter
  (the briefing/file was deleted)

Categories explicitly NOT auto-fixed:
- ``body_too_short`` — requires human review
- ``missing_type`` / ``missing_tags`` — requires human judgement
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from mnemo.autopilot.core import pr_budget
from mnemo.autopilot.core.labels import SELF_FIX_LABEL
from mnemo.autopilot.selffix import _gh
from mnemo.autopilot.selffix._perimeter import assert_perimeter

# Kinds that the fixer knows how to handle mechanically.
_AUTO_FIXABLE_KINDS = frozenset({"source_path_missing"})

_SHARED_SUBTYPES = ("feedback", "user", "reference")


@dataclass
class DoctorWarning:
    """A single auto-fixable (or not) doctor warning."""

    kind: str
    rule_path: Path
    detail: str
    auto_fixable: bool = field(init=False)

    def __post_init__(self) -> None:
        self.auto_fixable = self.kind in _AUTO_FIXABLE_KINDS


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_fixable(*, vault_root: Path) -> List[DoctorWarning]:
    """Scan ``shared/`` for auto-fixable doctor warnings.

    Currently detects: ``source_path_missing`` — a ``sources`` entry pointing
    to a file that no longer exists under *vault_root*.
    """
    from mnemo.core.filters import is_consumer_visible, parse_frontmatter

    shared = vault_root / "shared"
    if not shared.is_dir():
        return []

    warnings: List[DoctorWarning] = []
    for subtype in _SHARED_SUBTYPES:
        type_dir = shared / subtype
        if not type_dir.is_dir():
            continue
        for md_path in sorted(type_dir.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                fm = parse_frontmatter(text)
            except Exception:
                fm = {}
            if not fm:
                continue
            if not is_consumer_visible(md_path, fm, vault_root):
                continue  # skip drafts / transient artefacts
            sources = fm.get("sources") or []
            for src in sources:
                if not isinstance(src, str):
                    continue
                if not (vault_root / src).is_file():
                    warnings.append(
                        DoctorWarning(
                            kind="source_path_missing",
                            rule_path=md_path,
                            detail=src,
                        )
                    )
    # Return only auto-fixable ones
    return [w for w in warnings if w.auto_fixable]


# ---------------------------------------------------------------------------
# Fixing
# ---------------------------------------------------------------------------


def fix_warning(warning: DoctorWarning, *, vault_root: Path) -> Path:
    """Apply the mechanical fix for *warning* in-place.

    Returns the path of the modified file.
    Raises ``ValueError`` for unrecognised kinds.
    """
    if warning.kind == "source_path_missing":
        return _fix_source_path_missing(warning.rule_path, warning.detail)
    raise ValueError(f"No fixer for kind {warning.kind!r}")


def _fix_source_path_missing(rule_path: Path, missing_source: str) -> Path:
    """Strip the orphan source line from the rule's frontmatter."""
    text = rule_path.read_text(encoding="utf-8", errors="replace")
    # Match the YAML list item "  - <missing_source>" and remove it
    # We handle both leading spaces and tabs (YAML style).
    pattern = re.compile(
        r"^[ \t]*-[ \t]+" + re.escape(missing_source) + r"[ \t]*\n?",
        re.MULTILINE,
    )
    new_text = pattern.sub("", text)
    rule_path.write_text(new_text, encoding="utf-8")
    return rule_path


# ---------------------------------------------------------------------------
# pytest helper (mockable in tests)
# ---------------------------------------------------------------------------


def _run_pytest(*, repo_root: Path) -> bool:
    """Run pytest in *repo_root*.  Returns True iff exit code is 0 or 5.

    Exit code 5 ("no tests collected") is treated as success: when the autopilot
    runs from a vault directory with no test suite, pytest's empty-collection
    exit must not block a vault-only doctor fix. Real test failures (exit 1)
    still abort the PR.
    """
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "-q", "--tb=short"],
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


def open_doctor_fix_pr(
    warnings: List[DoctorWarning],
    *,
    vault_root: Path,
    repo_root: Path,
    dry_run: bool = False,
) -> Optional[int]:
    """Apply *warnings* and open a self-fix PR.

    Returns the PR number on success, ``None`` when skipped (dry-run, budget
    exhausted, gh unavailable, or pytest fails).
    """
    if not warnings:
        return None

    ok, reason = pr_budget.can_open(vault_root=vault_root, category="doctor_self_fix")
    if not ok:
        print(f"[autopilot] doctor fix skipped: {reason}")
        return None

    # Apply fixes in-place first (we need the diff to check perimeter)
    modified: List[Path] = []
    for w in warnings:
        try:
            path = fix_warning(w, vault_root=vault_root)
            modified.append(path)
        except Exception as exc:
            print(f"[autopilot] failed to fix {w.rule_path.name}: {exc}")

    if not modified:
        return None

    # Perimeter guard — abort if any modified file is outside the safe set
    try:
        assert_perimeter(modified, repo_root=repo_root, vault_root=vault_root)
    except Exception as exc:
        print(f"[autopilot] perimeter violation, aborting PR: {exc}")
        return None

    if dry_run:
        print(f"[autopilot] dry-run: would open doctor-fix PR for {len(modified)} file(s)")
        for p in modified:
            print(f"  • {p}")
        return None

    # Branch
    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    branch = f"mnemo/self-fix/doctor-{date_tag}"
    if _gh.create_branch(branch, repo_root=repo_root) is None:
        print("[autopilot] doctor fix skipped: could not create branch")
        return None

    # Run pytest before pushing
    if not _run_pytest(repo_root=repo_root):
        print("[autopilot] doctor fix aborted: pytest failed after applying fixes")
        return None

    # Push + open PR
    _gh.push_branch(branch, repo_root=repo_root)
    body_lines = [f"Automated self-fix for {len(warnings)} doctor warning(s):\n"]
    for w in warnings:
        body_lines.append(f"- `{w.rule_path.name}`: {w.kind} ({w.detail})")
    body = "\n".join(body_lines)

    pr_number = _gh.open_pr(
        branch=branch,
        title=f"fix(autopilot): doctor self-fix {date_tag}",
        body=body,
        labels=[SELF_FIX_LABEL],
        draft=True,
        repo_root=repo_root,
    )
    if pr_number is not None:
        pr_budget.record_opened(
            vault_root=vault_root, category="doctor_self_fix", pr_number=pr_number
        )
        print(f"[autopilot] opened doctor-fix PR #{pr_number}")
    return pr_number
