"""``mnemo publish`` — the rules for the repo you are in, into the repo.

Writes ``<repo>/<SHARE_DIR>/<type>/<slug>.md`` in the portable form
:mod:`mnemo.core.share.format` defines, so a teammate's ``mnemo import`` can
stage them for review. ``mnemo export`` is the sibling: same selection, same
CLI shape, same manifest-and-staleness pattern; the difference is the
reader. An export is for tools that have no mnemo; a published tree is for
another vault, so it keeps provenance and the evidence quote and drops
nothing a reviewer needs.

Every decision lives here; :mod:`mnemo.cli.commands.publish` only prints.
``status`` and ``doctor`` call :func:`staleness`.

What is selected
----------------

The reflex's project scope: consumer-visible pages attributed to *project*
through ``projects_for_rule`` plus universal ones, restricted to *types*.

* **Default types: ``feedback`` only.** ``user`` pages carry names and
  emails and this tree gets committed and pushed, so they are opt-in and
  the CLI prints the same caveat ``export`` does. ``project`` pages are also
  opt-in: they are the repo's own notes (288 on this repo), a tree of that
  many files is unreviewable in a PR, and a newcomer's ``_inbox`` would fill
  with material they have to read past before they reach the corrections —
  the one thing a second contributor cannot re-derive on their own.
  ``--types feedback,project`` is one flag away.
* **Universal rules are in.** They are what this repo's reflex injects; a
  second contributor should get what the first one's Claude gets. The
  publisher's project name is what the provenance names, so the reader can
  tell where the rule was learned.

Ownership
---------

A re-publish prunes and overwrites only its own files. A tree file is ours
when its provenance names this vault's id **or** its slug is in this vault's
manifest. The second clause is the hop rule's cost: a rule this vault
imported from vault A and promoted re-publishes naming A, so by id alone the
next publish would refuse its own file. Anything else in the tree — another
contributor's rule, a hand-written page, a stray README — is never removed
and never overwritten; a collision on a selected slug is refused and
reported by path.

An empty selection publishes nothing and prunes nothing: a misresolved
project name must not empty a tree. ``--remove`` is the explicit way to
take this vault's files out.

Dates
-----

``to_portable`` stamps the page's date from the vault's own timestamps and
falls back to *today* only for a page that carries none. For such a page
the date already in the tree is passed back as *today*, so an unchanged
vault publishes byte-identical files on any later day, ``git status`` stays
clean, and :func:`staleness` does not drift with the calendar.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.filters import derive_rule_slug, is_consumer_visible, iter_shared_pages
from mnemo.core.reclassify_types import split_frontmatter
from mnemo.core.rule_activation import is_universal, projects_for_rule

DEFAULT_TYPES: tuple[str, ...] = ("feedback",)
MANIFEST_DIR_REL = ".mnemo/share"


@dataclass(frozen=True)
class SelectedPage:
    """A vault page publish carries: identity plus the text ``to_portable`` reads."""

    slug: str
    page_type: str
    path: Path
    text: str
    universal: bool
    source_count: int

    @property
    def rel(self) -> str:
        """Path inside the tree, forward slashes: ``<type>/<slug>.md``."""
        return f"{self.page_type}/{self.slug}.md"


@dataclass
class PublishReport:
    project: str
    repo_root: Path
    share_root: Path
    vault: str = ""
    rules: List[SelectedPage] = field(default_factory=list)
    written: List[str] = field(default_factory=list)      # new files, rel paths
    updated: List[str] = field(default_factory=list)      # ours, bytes changed
    unchanged: List[str] = field(default_factory=list)    # ours, bytes identical
    pruned: List[str] = field(default_factory=list)       # ours, no longer selected
    refused: List[Tuple[str, str]] = field(default_factory=list)  # (rel path, why)
    not_pages: List[str] = field(default_factory=list)    # strays in the tree, left alone
    user_pages: List[str] = field(default_factory=list)
    warning: Optional[str] = None  # export.print_export_notes reads this
    wrote: bool = False
    removed: bool = False

    @property
    def universal(self) -> int:
        return sum(1 for r in self.rules if r.universal)

    @property
    def published(self) -> int:
        """Rules the tree carries for this vault after the run."""
        return len(self.written) + len(self.updated) + len(self.unchanged)

    @property
    def changed(self) -> bool:
        return bool(self.written or self.updated or self.pruned)


# --------------------------------------------------------------------------
# manifest — <vault>/.mnemo/share/<project>.json
# --------------------------------------------------------------------------


def manifest_path(vault_root: Path, project: str) -> Path:
    return Path(vault_root) / MANIFEST_DIR_REL / f"{project}.json"


def read_manifest(vault_root: Path, project: str) -> Optional[dict]:
    try:
        data = json.loads(manifest_path(vault_root, project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("rules"), dict) else None


def write_manifest(vault_root: Path, project: str, *, vault: str, cwd: str, path: str,
                   rules: Dict[str, str]) -> None:
    data = {
        "cwd": cwd,
        "path": path,
        "vault": vault,
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rules": dict(rules),
    }
    atomic_write_bytes(manifest_path(vault_root, project),
                       (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def delete_manifest(vault_root: Path, project: str) -> bool:
    p = manifest_path(vault_root, project)
    if not p.exists():
        return False
    p.unlink()
    return True


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------


def select_pages(vault_root: Path, *, project: str, types: Sequence[str] = DEFAULT_TYPES,
                 universal_threshold: int = 2) -> List[SelectedPage]:
    """Consumer-visible pages in *project*'s reflex scope, restricted to *types*.

    Same walk as ``export.select.select_rules``; keeps the whole page text
    because ``to_portable`` reads it. Universal first, then most-sourced,
    then slug — the order the CLI lists them in."""
    vault_root = Path(vault_root)
    shared = vault_root / "shared"
    wanted = {types} if isinstance(types, str) else set(types)
    out: List[SelectedPage] = []
    for md in iter_shared_pages(vault_root, include_inbox=False):
        rel = md.relative_to(shared).parts
        if len(rel) != 2 or rel[0] not in wanted:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = split_frontmatter(text)
        if not fm or not is_consumer_visible(md, fm, vault_root):
            continue
        sources = [s for s in (fm.get("sources") or []) if isinstance(s, str)]
        projects = projects_for_rule(sources, frontmatter=fm)
        universal = is_universal(projects, universal_threshold)
        if project not in projects and not universal:
            continue
        out.append(SelectedPage(
            slug=derive_rule_slug(fm, md.stem),
            page_type=rel[0],
            path=md,
            text=text,
            universal=universal,
            source_count=len(sources),
        ))
    out.sort(key=lambda r: (not r.universal, -r.source_count, r.slug))
    return out


# --------------------------------------------------------------------------
# the plan — one walk shared by publish, dry-run and staleness
# --------------------------------------------------------------------------


@dataclass
class _Plan:
    vault: str
    share_root: Path
    to_write: Dict[str, str] = field(default_factory=dict)   # rel → text (new or updated)
    unchanged: List[str] = field(default_factory=list)
    written: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    pruned: List[Path] = field(default_factory=list)
    refused: List[Tuple[str, str]] = field(default_factory=list)
    not_pages: List[str] = field(default_factory=list)
    hashes: Dict[str, str] = field(default_factory=dict)     # slug → portable_hash


def _today() -> str:
    return date.today().isoformat()


def _rel(share_root: Path, path: Path) -> str:
    return path.relative_to(share_root).as_posix()


def _ours(rule, *, vault: str, manifest: Optional[dict]) -> bool:
    if rule.vault == vault:
        return True
    return bool(manifest and rule.slug in manifest["rules"])


def _plan(vault_root: Path, *, project: str, repo_root: Path, pages: List[SelectedPage],
          today: str) -> _Plan:
    from mnemo.core.share import format as fmt

    vault = fmt.vault_id(vault_root)
    share_root = Path(repo_root) / fmt.SHARE_DIR
    manifest = read_manifest(vault_root, project)
    plan = _Plan(vault=vault, share_root=share_root)

    existing: Dict[str, Tuple[Path, object]] = {
        _rel(share_root, p): (p, rule) for p, rule in fmt.iter_portable(share_root)
    }
    planned: set[str] = set()

    for page in pages:
        rel = page.rel
        planned.add(rel)
        here = existing.get(rel)
        if here is not None:
            path, rule = here
            if rule is None:
                plan.refused.append((rel, "not a mnemo page, left alone"))
                continue
            if not _ours(rule, vault=vault, manifest=manifest):
                plan.refused.append((rel, f"published by another vault ({rule.vault[:8]}), left alone"))
                continue
            # Pin the date the tree already carries: for a page with no vault
            # timestamp `today` is its date, and re-stamping it every run
            # would make an unchanged rule a diff.
            text = fmt.to_portable(page.text, vault=vault, project=project,
                                   today=rule.published_at or today)
            if text is None:
                plan.refused.append((rel, "vault page is not a rule page"))
                continue
            try:
                current = path.read_text(encoding="utf-8")
            except OSError:
                current = None
            if current == text:
                plan.unchanged.append(rel)
            else:
                # Changed content gets today's date if the page has no timestamp.
                fresh = fmt.to_portable(page.text, vault=vault, project=project, today=today)
                text = fresh if fresh is not None else text
                plan.to_write[rel] = text
                plan.updated.append(rel)
        else:
            text = fmt.to_portable(page.text, vault=vault, project=project, today=today)
            if text is None:
                plan.refused.append((rel, "vault page is not a rule page"))
                continue
            plan.to_write[rel] = text
            plan.written.append(rel)
        plan.hashes[page.slug] = fmt.portable_hash(text)

    for rel, (path, rule) in existing.items():
        if rel in planned:
            continue
        if rule is None:
            plan.not_pages.append(rel)
        elif _ours(rule, vault=vault, manifest=manifest):
            plan.pruned.append(path)
    return plan


def _remove_files(paths: List[Path], share_root: Path) -> None:
    for p in paths:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    _drop_empty_dirs(share_root)


def _drop_empty_dirs(share_root: Path) -> None:
    """Remove emptied ``<type>/`` directories, then the tree root if empty."""
    if not share_root.is_dir():
        return
    for d in sorted(share_root.iterdir()):
        if d.is_dir():
            try:
                d.rmdir()
            except OSError:
                pass
    try:
        share_root.rmdir()
    except OSError:
        pass


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------


def run_publish(
    vault_root: Path,
    *,
    project: str,
    repo_root: Path,
    types: Sequence[str] = DEFAULT_TYPES,
    dry_run: bool = False,
    today: Optional[str] = None,
    universal_threshold: int = 2,
    remove: bool = False,
) -> PublishReport:
    """Select, plan, write (or remove). Raises ``OSError`` on a write failure.

    *today* is the date stamped on a page that carries no vault timestamp
    (see the module docstring); ``None`` means the calendar date. *remove*
    deletes this vault's files and the manifest, nothing else's.
    """
    from mnemo.core.share import format as fmt

    vault_root = Path(vault_root)
    repo_root = Path(repo_root).resolve()
    share_root = repo_root / fmt.SHARE_DIR
    day = today or _today()
    report = PublishReport(project=project, repo_root=repo_root, share_root=share_root)
    report.vault = fmt.vault_id(vault_root)

    if remove:
        plan = _plan(vault_root, project=project, repo_root=repo_root, pages=[], today=day)
        report.pruned = [_rel(share_root, p) for p in plan.pruned]
        report.not_pages = plan.not_pages
        if not dry_run:
            _remove_files(plan.pruned, share_root)
            had_manifest = delete_manifest(vault_root, project)
            report.removed = bool(plan.pruned) or had_manifest
        else:
            report.removed = bool(plan.pruned) or read_manifest(vault_root, project) is not None
        return report

    report.rules = select_pages(vault_root, project=project, types=types,
                                universal_threshold=universal_threshold)
    if not report.rules:
        return report
    report.user_pages = [r.slug for r in report.rules if r.page_type == "user"]

    plan = _plan(vault_root, project=project, repo_root=repo_root, pages=report.rules, today=day)
    report.written, report.updated, report.unchanged = plan.written, plan.updated, plan.unchanged
    report.refused, report.not_pages = plan.refused, plan.not_pages
    report.pruned = [_rel(share_root, p) for p in plan.pruned]
    if dry_run:
        return report

    for rel, text in plan.to_write.items():
        atomic_write_bytes(share_root / rel, text.encode("utf-8"))
    _remove_files(plan.pruned, share_root)
    write_manifest(vault_root, project, vault=plan.vault, cwd=str(repo_root),
                   path=fmt.SHARE_DIR, rules=plan.hashes)
    report.wrote = True
    return report


def staleness(
    vault_root: Path,
    *,
    project: str,
    repo_root: Path,
    types: Sequence[str] = DEFAULT_TYPES,
    universal_threshold: int = 2,
) -> Optional[Tuple[int, int]]:
    """``(rules in the tree, rules that differ or are new)``, ``None`` when never published.

    Compares the manifest against what a publish would write now, the way
    ``export.manifest.staleness`` does; a rule that no longer selects counts
    as differing. Read-only: touches neither the tree nor the manifest.
    """
    vault_root = Path(vault_root)
    manifest = read_manifest(vault_root, project)
    if manifest is None:
        return None
    pages = select_pages(vault_root, project=project, types=types,
                         universal_threshold=universal_threshold)
    plan = _plan(vault_root, project=project, repo_root=Path(repo_root).resolve(),
                 pages=pages, today=_today())
    published = manifest["rules"]
    changed = sum(1 for slug, h in published.items() if plan.hashes.get(slug) != h)
    added = sum(1 for slug in plan.hashes if slug not in published)
    return len(published), changed + added


def share_dir_is_gitignored(repo_root: Path) -> bool:
    """True when the repo's ignore rules would hide ``SHARE_DIR`` — a tree
    nobody can commit is the export-into-``.claude/`` mistake again. False
    when git is missing or *repo_root* is not a repository."""
    from mnemo.core.share import format as fmt

    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "-q", fmt.SHARE_DIR],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0
