"""Rewrite citations of a briefing that ``migrate-worktree-briefings`` moved.

Moving ``bots/<proj>-<suffix>/briefings/sessions/<uuid>.md`` to
``bots/<proj>/briefings/sessions/<uuid>.md`` leaves every reference to the old
path dangling (#228). The move is the easy half; the citations are the half
that makes the move safe.

Four populations cite a briefing path, and a rewrite that misses any one of
them trades one breakage for another:

* **Rule markdown under ``shared/``** — in three spellings, not the two the
  issue reports: the frontmatter ``sources:`` list, ``evidence.source``
  (``source: 'briefing: bots/...'``), and ``[[wikilink]]`` bodies. Drafts in
  ``shared/_inbox/`` count: ``doctor`` cannot see them, so nothing else would
  ever report them.
* **``.mnemo/extraction-state.json``** — ``source_files`` is NOT the inert
  historical record it looks like. ``inbox/dedup.py`` compares it to a new
  page's sources for *exact set equality* to detect slug drift, so leaving it
  stale while the frontmatter moves makes the two disagree and silently
  defeats that guard. ``source_hash`` is derived from the same list, so it is
  recomputed in step with it.
* **The derived indexes** — ``rule-activation-index.json`` and the reflex
  index both derive a rule's *project* from the ``bots/<name>/`` segment of
  its sources (``rule_activation.projects_for_rule``). A stale ``-wt-`` source
  does not merely dangle: it files the rule under project ``mnemo-wt-187``,
  where no lookup for ``mnemo`` will ever find it. Rebuilding is therefore
  part of the repair, not a tidy-up.
* **``written_hash``** — a bulk rewriter owns the bytes it produces. Editing a
  page without advancing its recorded hash makes it read as "user edited" to
  the promote path, which stages a ``.proposed.md`` instead of updating in
  place. ``migrations/slugs.py`` records this as a mistake already made once;
  this module advances the hash for exactly the pages it rewrote.

Only the path *prefix* changes — the basename is preserved by the move — so
the rewrite needs no inference and never has to guess which briefing a
citation meant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_SESSIONS_SEGMENT = "briefings/sessions/"


@dataclass
class CitationPlan:
    """What a citation rewrite would change, before anything is written."""

    #: ``{old_vault_relative_path: new_vault_relative_path}``.
    path_map: dict[str, str] = field(default_factory=dict)
    #: Rule pages that cite a moved briefing → number of citations in each.
    pages: dict[Path, int] = field(default_factory=dict)
    #: ``extraction-state`` entry keys whose ``source_files`` cite one.
    state_keys: list[str] = field(default_factory=list)

    @property
    def citation_count(self) -> int:
        return sum(self.pages.values())

    def is_empty(self) -> bool:
        return not self.pages and not self.state_keys


def build_path_map(moves: list[tuple[Path, Path]], vault_root: Path) -> dict[str, str]:
    """Map each moved briefing's old vault-relative path to its new one.

    Paths are keyed in the POSIX, vault-relative spelling that
    ``extract/source_paths`` normalizes every stored source to, so a citation
    written on either OS compares equal to what is stored.
    """
    vault_root = Path(vault_root)
    out: dict[str, str] = {}
    for src, target in moves:
        try:
            old = Path(src).relative_to(vault_root).as_posix()
            new = Path(target).relative_to(vault_root).as_posix()
        except ValueError:
            # A move outside the vault is not ours to rewrite.
            continue
        if old != new:
            out[old] = new
    return out


def _rewrite_text(text: str, path_map: dict[str, str]) -> tuple[str, int]:
    """Return *text* with every mapped path replaced, and the hit count.

    A plain substring replace is correct here and a regex is not needed: the
    old path is a full vault-relative path ending in ``<uuid>.md``, which is
    specific enough that it cannot collide with prose. It matches in all three
    spellings at once — ``- bots/...`` list items, ``source: 'briefing:
    bots/...'`` and ``[[bots/...]]`` wikilinks (whose form omits the ``.md``,
    so that variant is tried too).
    """
    hits = 0
    for old, new in path_map.items():
        if old == new:
            continue
        # Rewrite the with-extension form first: replacing the bare stem first
        # would leave the trailing ``.md`` orphaned onto an already-new path.
        forms = [(old, new)]
        if old.endswith(".md") and new.endswith(".md"):
            forms.append((old[:-3], new[:-3]))
        for old_form, new_form in forms:
            count = text.count(old_form)
            if count:
                text = text.replace(old_form, new_form)
                hits += count
    return text, hits


def _iter_rule_pages(vault_root: Path):
    """Every markdown page under ``shared/``, drafts in ``_inbox/`` included.

    ``doctor`` only validates consumer-visible pages, which is why the
    ``_inbox`` citations went unreported in #228. A migration has no such
    excuse: a draft that cites a path which no longer exists is promoted with
    that dead path intact.
    """
    shared = Path(vault_root) / "shared"
    if not shared.is_dir():
        return
    yield from sorted(shared.rglob("*.md"))


def plan_citations(vault_root: Path, path_map: dict[str, str]) -> CitationPlan:
    """Find every citation of a moved briefing, without writing anything."""
    plan = CitationPlan(path_map=dict(path_map))
    if not path_map:
        return plan

    for md in _iter_rule_pages(vault_root):
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        _new, hits = _rewrite_text(text, path_map)
        if hits:
            plan.pages[md] = hits

    state_path = Path(vault_root) / ".mnemo" / "extraction-state.json"
    if state_path.is_file():
        try:
            from mnemo.core.extract.inbox.state_io import load_state

            state = load_state(state_path)
        except Exception:  # noqa: BLE001 — a broken state file is not ours to fix
            return plan
        for key, entry in state.entries.items():
            if any(s in path_map for s in (entry.source_files or [])):
                plan.state_keys.append(key)
    return plan


def apply_citations(vault_root: Path, plan: CitationPlan) -> int:
    """Rewrite every planned citation. Returns the number of pages changed.

    Each page is written atomically and its ``written_hash`` advanced, so the
    promote path keeps recognising the page as machine-owned.
    """
    if plan.is_empty():
        return 0

    from mnemo.core.atomic import atomic_write_bytes

    changed_pages: dict[str, Path] = {}
    written = 0
    for md in sorted(plan.pages):
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new_text, hits = _rewrite_text(text, plan.path_map)
        if not hits or new_text == text:
            continue
        atomic_write_bytes(md, new_text.encode("utf-8"))
        written += 1
        key = _state_key_for(md, Path(vault_root))
        if key:
            changed_pages[key] = md

    _rewrite_state(vault_root, plan, changed_pages)
    return written


def _state_key_for(md: Path, vault_root: Path) -> str | None:
    """``"<type>/<slug>"`` for a page under ``shared/``, mirroring the state key."""
    shared = Path(vault_root) / "shared"
    try:
        parts = md.relative_to(shared).parts
    except ValueError:
        return None
    parts = parts[1:] if parts and parts[0] == "_inbox" else parts
    if len(parts) < 2:
        return None
    stem = md.stem
    if stem.endswith(".proposed"):
        stem = stem[: -len(".proposed")]
    return f"{parts[0]}/{stem}"


def _rewrite_state(vault_root: Path, plan: CitationPlan, changed_pages: dict[str, Path]) -> None:
    """Repoint ``source_files`` and re-sync the hashes of pages we rewrote.

    ``source_hash`` is derived from ``sorted(source_files) + body``, and
    ``apply.py`` skips a page whose recorded ``source_hash`` still matches. If
    the list is repointed without recomputing it, the next extraction compares
    a new hash against a stale one and needlessly rewrites the page; if it is
    recomputed without repointing the list, ``dedup`` stops matching. Both
    move together or neither does.
    """
    state_path = Path(vault_root) / ".mnemo" / "extraction-state.json"
    if not state_path.is_file():
        return
    try:
        from mnemo.core.extract.inbox.io import content_hash
        from mnemo.core.extract.inbox.state_io import atomic_write_state, load_state

        state = load_state(state_path)
    except Exception:  # noqa: BLE001
        return

    dirty = False
    for key, entry in state.entries.items():
        sources = list(entry.source_files or [])
        if any(s in plan.path_map for s in sources):
            entry.source_files = [plan.path_map.get(s, s) for s in sources]
            dirty = True
        page = changed_pages.get(key)
        if page is not None and page.is_file():
            try:
                entry.written_hash = content_hash(page.read_text(encoding="utf-8"))
                dirty = True
            except (OSError, UnicodeDecodeError):
                pass

    if dirty:
        atomic_write_state(state, state_path)


def rebuild_indexes(vault_root: Path) -> list[str]:
    """Rebuild both project-derived indexes. Returns the names rebuilt.

    Both derive a rule's project from the ``bots/<name>/`` segment of its
    sources, so until they are rebuilt a migrated rule stays filed under the
    old worktree name. Best-effort, matching ``reclassify_apply``: a failed
    index rebuild is logged, never fatal — the vault content is already
    correct and the next extraction rebuilds.
    """
    from mnemo.core import errors

    done: list[str] = []
    try:
        from mnemo.core import rule_activation

        rule_activation.write_index(vault_root, rule_activation.build_index(vault_root))
        done.append("rule-activation")
    except Exception as exc:  # noqa: BLE001
        errors.log_error(vault_root, "migrate_worktree_briefings.rule_activation_index", exc)
    try:
        from mnemo.core.reflex import index as reflex_index

        reflex_index.write_index(vault_root, reflex_index.build_index(vault_root))
        done.append("reflex")
    except Exception as exc:  # noqa: BLE001
        errors.log_error(vault_root, "migrate_worktree_briefings.reflex_index", exc)
    return done


__all__ = [
    "CitationPlan",
    "apply_citations",
    "build_path_map",
    "plan_citations",
    "rebuild_indexes",
]
