"""Autopilot Tier 1 — Doctor self-fix.

Detects auto-fixable doctor warnings and can open a self-fix PR.

Auto-fixable categories:
- ``source_path_missing`` — strip the source line from frontmatter
  (the briefing/file was deleted), unless it is the rule's only source:
  emptying ``sources:`` orphans the rule from per-project scoping
- ``source_path_moved`` — re-point a dead source at the path the briefing
  now lives under (also repairs wrong project attribution)
- ``source_path_absolute`` — relativize a machine-absolute source path that
  still resolves under the vault
- ``sources_empty`` — re-populate an empty ``sources:`` block from
  extraction state (heals rules orphaned before that guard existed)

Categories explicitly NOT auto-fixed:
- ``body_too_short`` — requires human review
- ``missing_type`` / ``missing_tags`` — requires human judgement
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from mnemo._selfexec import python_argv
from mnemo.autopilot.core import network, pr_budget
from mnemo.autopilot.core.labels import SELF_FIX_LABEL
from mnemo.autopilot.selffix import _gh
from mnemo.autopilot.selffix._perimeter import assert_perimeter
from mnemo.core.extract.source_paths import vault_relative_source

# Kinds that the fixer knows how to handle mechanically.
_AUTO_FIXABLE_KINDS = frozenset({
    "source_path_missing", "source_path_moved", "source_path_absolute", "sources_empty",
})

_SHARED_SUBTYPES = ("feedback", "user", "reference", "project")


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

    Detects ``source_path_absolute`` (machine-absolute path that still
    resolves under the vault), ``source_path_moved`` (dead path, briefing
    found elsewhere), ``source_path_missing`` (dead path, nothing to relocate
    to, and at least one other source survives) and ``sources_empty`` (an
    empty ``sources:`` block that extraction state can still repopulate).
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
            sources = [s for s in (fm.get("sources") or []) if isinstance(s, str)]
            if not sources:
                recovered = _sources_from_state(vault_root, subtype, md_path.stem)
                if recovered:
                    warnings.append(
                        DoctorWarning(
                            kind="sources_empty",
                            rule_path=md_path,
                            detail="\n".join(recovered),
                        )
                    )
                continue
            # Absolute paths under the vault resolve as files (pathlib lets an
            # absolute right-hand operand win), so they never surface as
            # "missing" — but they are machine-brittle and must be relativized.
            for src in sources:
                rel = vault_relative_source(src, vault_root)
                if rel != src and (vault_root / rel).is_file():
                    warnings.append(
                        DoctorWarning(
                            kind="source_path_absolute",
                            rule_path=md_path,
                            detail=f"{src}\n{rel}",
                        )
                    )

            missing = [s for s in sources if not (vault_root / s).is_file()]
            relocatable = any(_relocate_source(vault_root, s) for s in missing)
            if len(missing) >= len(sources) and not relocatable:
                # Stripping every source would leave `sources:` empty: the rule
                # loses its provenance, resolves to zero projects, and falls out
                # of per-project scoping. That trades one warning for a worse
                # one, so it is not auto-fixable — a human re-points the source.
                continue
            for src in missing:
                moved = _relocate_source(vault_root, src)
                if moved:
                    warnings.append(
                        DoctorWarning(
                            kind="source_path_moved",
                            rule_path=md_path,
                            detail=f"{src}\n{moved}",
                        )
                    )
                else:
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
    if warning.kind in ("source_path_moved", "source_path_absolute"):
        old_src, new_src = warning.detail.split("\n", 1)
        return _fix_source_path_moved(warning.rule_path, old_src, new_src)
    if warning.kind == "sources_empty":
        return _fix_sources_empty(warning.rule_path, warning.detail.splitlines())
    raise ValueError(f"No fixer for kind {warning.kind!r}")


def _relocate_source(vault_root: Path, src: str) -> str | None:
    """Return the vault-relative path a dead ``sources`` entry now lives at.

    Two drifts produce dead-but-recoverable paths: the pre-worktree layout
    wrote ``briefings/sessions/<id>.md`` without the ``bots/<project>/``
    prefix, and a session analysed from the wrong cwd recorded the wrong
    project. Both keep the filename, so the basename locates the survivor —
    and relocating (rather than stripping) also repairs the project
    attribution that per-project scoping reads.

    Returns None when there is no match or more than one: an ambiguous
    relocation would silently attribute a rule to an arbitrary project.
    """
    name = Path(src).name
    if not name:
        return None
    # Some entries recorded the bare session id, without the .md suffix.
    candidates = [name] if name.endswith(".md") else [name, f"{name}.md"]
    for candidate in candidates:
        matches = [
            p for p in vault_root.glob(f"bots/*/briefings/sessions/{candidate}")
            if p.is_file()
        ]
        if len(matches) == 1:
            return matches[0].relative_to(vault_root).as_posix()
        if matches:
            return None  # ambiguous — a human decides
    return None


def _sources_from_state(vault_root: Path, page_type: str, stem: str) -> list[str]:
    """Return still-resolvable ``source_files`` recorded for ``<type>/<stem>``.

    Extraction state keeps a rule's provenance even when the rule file lost
    it, which is how rules orphaned by the pre-guard strip-fixer can be
    healed. A recorded path that no longer resolves is relocated by basename
    when possible and dropped otherwise — restoring a dead path would just
    re-raise ``source_path_missing``.
    """
    import json

    state_path = vault_root / ".mnemo" / "extraction-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    entry = (state.get("entries") or {}).get(f"{page_type}/{stem}")
    if not isinstance(entry, dict):
        return []
    out: list[str] = []
    for s in entry.get("source_files") or []:
        if not isinstance(s, str):
            continue
        if (vault_root / s).is_file():
            out.append(s)
            continue
        moved = _relocate_source(vault_root, s)
        if moved:
            out.append(moved)
    return out


def _fix_source_path_moved(rule_path: Path, old_src: str, new_src: str) -> Path:
    """Re-point a dead ``sources`` line at the path the briefing moved to."""
    text = rule_path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        r"^([ \t]*-[ \t]+)" + re.escape(old_src) + r"[ \t]*$",
        re.MULTILINE,
    )
    new_text, count = pattern.subn(lambda m: m.group(1) + new_src, text)
    if count == 0:
        raise ValueError(f"source line {old_src!r} not found in {rule_path.name}")
    rule_path.write_text(new_text, encoding="utf-8")
    return rule_path


def _fix_sources_empty(rule_path: Path, sources: list[str]) -> Path:
    """Re-populate an empty ``sources:`` block from recovered paths."""
    if not sources:
        raise ValueError(f"no recoverable sources for {rule_path.name}")
    text = rule_path.read_text(encoding="utf-8", errors="replace")
    block = "sources:\n" + "".join(f"  - {s}\n" for s in sources)
    new_text, count = re.subn(r"^sources:[ \t]*\n", block, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"no empty sources block found in {rule_path.name}")
    rule_path.write_text(new_text, encoding="utf-8")
    return rule_path


def _fix_source_path_missing(rule_path: Path, missing_source: str) -> Path:
    """Strip the orphan source line from the rule's frontmatter.

    Refuses when it is the rule's only source — see ``detect_fixable``: an
    empty ``sources:`` block orphans the rule from project scoping.
    """
    from mnemo.core.filters import parse_frontmatter

    text = rule_path.read_text(encoding="utf-8", errors="replace")
    try:
        sources = [s for s in (parse_frontmatter(text).get("sources") or [])
                   if isinstance(s, str)]
    except Exception:
        sources = []
    if len(sources) <= 1:
        raise ValueError(
            f"refusing to strip the only source of {rule_path.name} "
            f"({missing_source!r}) — re-point it manually"
        )
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

    Returns False when no Python interpreter can be found. Under a frozen
    build ``sys.executable`` is the mnemo binary, so it cannot run pytest —
    and failing closed is right: this gate exists to stop an unverified fix
    from becoming a PR.
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


def open_doctor_fix_pr(
    warnings: List[DoctorWarning],
    *,
    vault_root: Path,
    repo_root: Optional[Path],
    dry_run: bool = False,
) -> Optional[int]:
    """Apply *warnings* and open a self-fix PR.

    The cures are applied to the live vault either way; *repo_root* only
    controls whether they can also become a PR. Pass ``None`` when the vault
    is not under version control — the vault is still healed, silently
    skipping the PR is the only sane alternative to branching a repo that
    does not contain the edited files.

    Returns the PR number on success, ``None`` when skipped (dry-run, budget
    exhausted, no repo, gh unavailable, empty diff, or pytest fails).
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
        assert_perimeter(
            modified, repo_root=repo_root or vault_root, vault_root=vault_root
        )
    except Exception as exc:
        print(f"[autopilot] perimeter violation, aborting PR: {exc}")
        return None

    if dry_run:
        print(f"[autopilot] dry-run: would open doctor-fix PR for {len(modified)} file(s)")
        for p in modified:
            print(f"  • {p}")
        return None

    if repo_root is None:
        print(
            f"[autopilot] doctor fix applied to {len(modified)} file(s); "
            "vault is not a git repo, no PR opened"
        )
        return None

    if not network.enabled():
        print(
            f"[autopilot] {len(modified)} fix(es) applied in place; "
            "network off (autopilot.network.enabled=false), no PR opened"
        )
        return None

    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    branch = f"mnemo/self-fix/doctor-{date_tag}"
    title = f"fix(autopilot): doctor self-fix {date_tag}"

    # Everything below happens in a throwaway worktree — the live checkout,
    # its HEAD and its index are never touched.
    worktree = _gh.create_worktree(branch, repo_root=repo_root)
    if worktree is None:
        print("[autopilot] doctor fix skipped: could not create worktree")
        return None

    try:
        _gh.mirror_paths(modified, source_root=repo_root, worktree=worktree)
        if not _gh.commit_all(title, worktree=worktree):
            print("[autopilot] doctor fix skipped: nothing to commit")
            return None

        if not _run_pytest(repo_root=worktree):
            print("[autopilot] doctor fix aborted: pytest failed after applying fixes")
            return None

        if not _gh.push_branch(branch, repo_root=worktree):
            print("[autopilot] doctor fix aborted: could not push branch")
            return None

        body_lines = [f"Automated self-fix for {len(warnings)} doctor warning(s):\n"]
        for w in warnings:
            body_lines.append(f"- `{w.rule_path.name}`: {w.kind} ({w.detail})")
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
            vault_root=vault_root, category="doctor_self_fix", pr_number=pr_number
        )
        print(f"[autopilot] opened doctor-fix PR #{pr_number}")
    return pr_number
