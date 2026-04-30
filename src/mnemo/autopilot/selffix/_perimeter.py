"""Perimeter guard — ensures auto-PRs only touch safe directories.

Every PR-opening function must call ``assert_perimeter`` with the list
of paths it intends to modify. The guard raises ``PerimeterViolation``
if any path falls outside the allowed set.
"""
from __future__ import annotations

from pathlib import Path

ALLOWED_PATHS = {
    "shared/",
    ".mnemo/",
    "docs/",
    "briefings/",
    "src/mnemo/autopilot/_archive/",
}


class PerimeterViolation(ValueError):
    """Raised when a proposed diff contains a path outside the perimeter."""


def _relative_str(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def is_within_perimeter(path: Path, *, repo_root: Path) -> bool:
    """Return True iff *path* is inside one of the :data:`ALLOWED_PATHS`."""
    rel = _relative_str(path, repo_root)
    for prefix in ALLOWED_PATHS:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            return True
    return False


def assert_perimeter(diff: list[Path], *, repo_root: Path) -> None:
    """Raise :exc:`PerimeterViolation` if any path in *diff* is outside the
    allowed perimeter.

    Args:
        diff: List of absolute or repo-relative paths that would be modified.
        repo_root: Root of the repository (used to compute relative paths).
    """
    violations: list[str] = []
    for path in diff:
        if not is_within_perimeter(path, repo_root=repo_root):
            violations.append(_relative_str(path, repo_root))
    if violations:
        raise PerimeterViolation(
            f"Perimeter violation — paths outside allowed dirs: {', '.join(violations)}"
        )
