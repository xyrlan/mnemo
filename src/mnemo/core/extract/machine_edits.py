"""Machine edits to pages the extraction ledger tracks (#470).

``written_hash`` in ``.mnemo/extraction-state.json`` is how extraction tells
whether a person edited a page it wrote: when the bytes on disk no longer hash
to it, ``promote.py`` and ``inbox_flow`` keep the page and stage the update as
a ``.proposed.md`` sibling instead. So every tool that rewrites a tracked page
has to advance the hash with its write, or its own edit reads as the user's —
the update is diverted, and a real user edit on that page can no longer be
told apart from the tool's.

Two writers skipped that and drifted most of a real vault (2026-09-23: 423 of
481 live project pages, 128 staged pages): the doctor self-fixer relativizing
``sources:``, and ``tools/measure_demotions.py --stamp --apply`` inserting
``reference_gate:``. This module holds both halves of the fix:

* :func:`edit_session` — the one door a tool uses to rewrite tracked pages. It
  takes the extraction lock, and advances ``written_hash`` for a page only when
  the page was the extractor's own bytes before the write. A page a person had
  already edited keeps its drift, so their edit stays protected.
* :func:`rebaseline` — heals the drift those writers already left. A drifted
  page is re-baselined only when *undoing* known machine edits reproduces the
  recorded hash exactly; anything else may be a person's edit and is left.
"""
from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from itertools import combinations, product
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from mnemo.core import locks
from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.extract.inbox.io import atomic_write, content_hash
from mnemo.core.extract.scanner import ExtractionState, StateEntry

STATE_REL = ".mnemo/extraction-state.json"
LOCK_REL = ".mnemo/extract.lock"

_PROPOSED = ".proposed.md"


class VaultBusy(RuntimeError):
    """An extraction holds the vault; writing under it would race its state."""


def page_key(page: Path, vault_root: Path) -> Optional[str]:
    """The ledger key (``<type>/<slug>``) of a page under ``shared/``, or None.

    Same derivation as the slug migration, which mirrors what ``apply_pages``
    and ``promote_projects`` install: staged and live pages share one key.
    """
    from mnemo.core.migrations.slugs import _key_for

    try:
        return _key_for(Path(page), Path(vault_root) / "shared")
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Writing: the door every out-of-band writer goes through
# ---------------------------------------------------------------------------


@dataclass
class EditSession:
    """Writes pages and keeps ``written_hash`` in step with each write."""

    vault_root: Path
    state: Optional[ExtractionState]
    #: Keys whose ``written_hash`` moved with this session's write.
    advanced: List[str] = field(default_factory=list)
    #: Keys whose page had drifted before the write: a person's edit (or an
    #: unknown writer's) is on it, so the hash stays and the page stays theirs.
    drifted: List[str] = field(default_factory=list)
    #: Pages with no ledger entry — nothing extraction would compare against.
    untracked: List[Path] = field(default_factory=list)

    def write(self, page: Path, data: Union[str, bytes]) -> None:
        page = Path(page)
        before = content_hash(page) if page.is_file() else None
        if isinstance(data, str):
            atomic_write(page, data)
            after = content_hash(data)
        else:
            atomic_write_bytes(page, data)
            after = content_hash(data)
        self._note(page, before, after)

    def _note(self, page: Path, before: Optional[str], after: str) -> None:
        key = page_key(page, self.vault_root)
        entry = self.state.entries.get(key) if (self.state and key) else None
        if entry is None:
            self.untracked.append(page)
        elif before is None or before != entry.written_hash:
            self.drifted.append(key)
        else:
            entry.written_hash = after
            self.advanced.append(key)


@contextlib.contextmanager
def edit_session(vault_root: Path) -> Iterator[EditSession]:
    """Hold the extraction lock and yield an :class:`EditSession`.

    The state is saved on the way out even when the body raised, so every page
    already written keeps its hash in step. Raises :class:`VaultBusy` when an
    extraction holds the lock: it loaded the state before us and would write
    its own copy back over ours.
    """
    from mnemo.core.extract.inbox.state_io import (
        StateSchemaError,
        atomic_write_state,
        load_state,
    )

    vault_root = Path(vault_root)
    lock_path = vault_root / LOCK_REL
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with locks.try_lock(lock_path) as held:
        if not held:
            raise VaultBusy("an extraction is in progress (lock held); try again later")
        state_path = vault_root / STATE_REL
        # A state this mnemo cannot read is not ours to rewrite: the edit
        # still happens, as it did before #470, with no hash to move.
        try:
            state = load_state(state_path) if state_path.is_file() else None
        except StateSchemaError:
            state = None
        session = EditSession(vault_root=vault_root, state=state)
        try:
            yield session
        finally:
            if state is not None and session.advanced:
                atomic_write_state(state, state_path)


# ---------------------------------------------------------------------------
# Known machine edits, and how to undo each one
# ---------------------------------------------------------------------------


def _fm_bounds(text: str) -> Optional[Tuple[int, int]]:
    """(start of the first key line, start of the closing ``---`` line)."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    return 4, end + 1


def _drop_fm_line(text: str, prefix: str) -> Optional[str]:
    """*text* without its first frontmatter line starting with *prefix*."""
    bounds = _fm_bounds(text)
    if bounds is None:
        return None
    start, close = bounds
    at = start
    while at < close:
        nl = text.find("\n", at, close)
        stop = close if nl == -1 else nl + 1
        if text.startswith(prefix, at):
            return text[:at] + text[stop:]
        at = stop
    return None


def unstamp_reference_gate(text: str) -> Optional[str]:
    """Undo ``measure_demotions --stamp``: it inserted one ``reference_gate:``
    line and moved no other byte."""
    return _drop_fm_line(text, "reference_gate:")


def unstamp_slug(text: str) -> Optional[str]:
    """Undo the #114 ``slug:`` stamp (see ``migrations.slugs._unstamp``)."""
    from mnemo.core.migrations.slugs import _unstamp

    return _unstamp(text)


_SOURCE_ITEM = re.compile(r"^([ \t]+-[ \t]+)(.*?)([ \t]*)$")


def absolutize_sources(text: str, prefix: str) -> Optional[str]:
    """Undo the ``sources:`` relativization, with *prefix* as the vault path.

    ``promote.py`` rendered each project page's source as the scanner's
    absolute path, and the doctor fixer (``source_path_absolute``) rewrote it
    to ``bots/...``. Only items of the frontmatter's ``sources:`` block are
    touched. A prefix spelled with backslashes (a Windows vault) is rejoined
    with them, as ``str(Path)`` rendered it there.
    """
    bounds = _fm_bounds(text)
    if bounds is None:
        return None
    start, close = bounds
    sep = "\\" if ("\\" in prefix and "/" not in prefix) else "/"
    lines = text[start:close].split("\n")
    out: List[str] = []
    in_sources = changed = False
    for line in lines:
        if line and not line[0].isspace():
            in_sources = line.rstrip() == "sources:"
        elif in_sources:
            m = _SOURCE_ITEM.match(line)
            if m and m.group(2).startswith("bots/"):
                rel = m.group(2) if sep == "/" else m.group(2).replace("/", "\\")
                line = f"{m.group(1)}{prefix.rstrip(sep)}{sep}{rel}{m.group(3)}"
                changed = True
        out.append(line)
    if not changed:
        return None
    return text[:start] + "\n".join(out) + text[close:]


_ABS_SOURCE = re.compile(r"^[ \t]+-[ \t]+(.+?)[/\\]bots[/\\]", re.MULTILINE)


def vault_prefixes(vault_root: Path, texts: Sequence[str] = ()) -> List[str]:
    """Every spelling of the vault path a page's absolute source may carry.

    The vault root as configured and resolved, plus any prefix still found on
    a page that kept its absolute ``sources:`` — a vault that moved, or one
    reached through a symlink, rendered a path neither of the first two spell.
    """
    seen: Dict[str, None] = {}
    root = Path(vault_root)
    for p in (str(root), str(root.resolve())):
        seen.setdefault(p, None)
    for text in texts:
        bounds = _fm_bounds(text)
        if bounds is None:
            continue
        for m in _ABS_SOURCE.finditer(text[: bounds[1]]):
            seen.setdefault(m.group(1), None)
    return list(seen)


def _reversals(prefixes: Sequence[str]) -> List[Tuple[str, List[Callable[[str], Optional[str]]]]]:
    return [
        ("sources", [lambda t, p=p: absolutize_sources(t, p) for p in prefixes]),
        ("reference_gate", [unstamp_reference_gate]),
        ("slug", [unstamp_slug]),
    ]


def explain_drift(
    text: str, written_hash: str, prefixes: Sequence[str],
) -> Optional[str]:
    """Name the known machine edits that account for *text*'s drift, or None.

    Every combination of the known edits is undone and the result hashed; a
    match means the only changes since the extractor wrote the page were
    those edits. The name is ``+``-joined, e.g. ``"sources+reference_gate"``.
    A reworded body, a hand-added tag — anything else fails every candidate.
    """
    kinds = _reversals(prefixes)
    for size in range(1, len(kinds) + 1):
        for combo in combinations(kinds, size):
            for undo in product(*(fns for _, fns in combo)):
                candidate: Optional[str] = text
                for fn in undo:
                    candidate = fn(candidate) if candidate is not None else None
                if candidate is not None and content_hash(candidate) == written_hash:
                    return "+".join(name for name, _ in combo)
    return None


# ---------------------------------------------------------------------------
# Re-baseline: heal the drift the writers already left
# ---------------------------------------------------------------------------


@dataclass
class RebaselineReport:
    in_sync: int = 0
    #: (key, edits) whose ``written_hash`` moved to the bytes on disk.
    rebaselined: List[Tuple[str, str]] = field(default_factory=list)
    #: Drifted keys no known edit explains — possibly a person's edit. Untouched.
    left: List[str] = field(default_factory=list)
    #: Keys whose diverted ``.proposed.md`` was applied to the re-baselined page.
    siblings_applied: List[str] = field(default_factory=list)


def _pages_for(key: str, entry: StateEntry, shared: Path) -> List[Path]:
    ty, _, slug = key.partition("/")
    live, staged = shared / ty / f"{slug}.md", shared / "_inbox" / ty / f"{slug}.md"
    order = (staged, live) if entry.status == "inbox" else (live, staged)
    return [p for p in order if p.is_file()]


def _diverted_sibling(key: str, entry: StateEntry, page: Path, shared: Path) -> Optional[Path]:
    """The ``.proposed.md`` an update was diverted into because of the drift.

    Only the two branches that divert on drift, and only where their sibling
    cannot be something else:

    * ``promote.py`` — a live project page's sibling in ``_inbox/project/``;
      no other writer stages a project proposal.
    * ``inbox_flow`` — a staged page's adjacent sibling, when the slug has no
      live page (a live page's ``.proposed.md`` there could be an upgrade
      proposal, which is for a person to review).
    """
    ty, _, slug = key.partition("/")
    if ty == "project" and page == shared / "project" / f"{slug}.md":
        return shared / "_inbox" / "project" / f"{slug}{_PROPOSED}"
    if (entry.status == "inbox"
            and page == shared / "_inbox" / ty / f"{slug}.md"
            and not (shared / ty / f"{slug}.md").exists()):
        return page.with_name(f"{slug}{_PROPOSED}")
    return None


def rebaseline(
    vault_root: Path, state: ExtractionState, *, dry_run: bool = False,
) -> RebaselineReport:
    """Re-baseline every entry whose drift known machine edits explain exactly.

    Mutates *state* in memory (the caller saves it), so extraction can run it
    on the state it already loaded; a separate load-and-save would be
    overwritten by extraction's own write at the end of the run.

    A re-baselined page whose update was already diverted into a sibling gets
    that update now: the sibling is what the overwrite branch would have
    written had the drift not been there, and nothing of a person's is on the
    page it replaces. The sibling is moved onto the page, not copied, so the
    proposal does not stay in the inbox as a second copy.

    Idempotent: a page it heals is in sync on the next call.
    """
    vault_root = Path(vault_root)
    shared = vault_root / "shared"
    report = RebaselineReport()

    drifted: List[Tuple[str, StateEntry, List[Tuple[Path, str]]]] = []
    texts: List[str] = []
    for key, entry in state.entries.items():
        if not entry.written_hash:
            continue
        pages = []
        for page in _pages_for(key, entry, shared):
            try:
                pages.append((page, page.read_bytes().decode("utf-8")))
            except (OSError, UnicodeDecodeError):
                continue
        if not pages:
            continue
        texts.extend(t for _, t in pages)
        if any(content_hash(t) == entry.written_hash for _, t in pages):
            report.in_sync += 1
        else:
            drifted.append((key, entry, pages))

    prefixes = vault_prefixes(vault_root, texts)
    for key, entry, pages in drifted:
        for page, text in pages:
            edits = explain_drift(text, entry.written_hash, prefixes)
            if edits is None:
                continue
            report.rebaselined.append((key, edits))
            sibling = _diverted_sibling(key, entry, page, shared)
            if sibling is not None and sibling.is_file():
                report.siblings_applied.append(key)
            if dry_run:
                break
            entry.written_hash = content_hash(text)
            if sibling is not None and sibling.is_file():
                proposal = sibling.read_bytes()
                atomic_write_bytes(page, proposal)
                entry.written_hash = content_hash(proposal)
                sibling.unlink()
            break
        else:
            report.left.append(key)
    return report


def summary_line(report: RebaselineReport) -> str:
    """One line for the extraction summary and doctor."""
    by_edit: Dict[str, int] = {}
    for _, edits in report.rebaselined:
        by_edit[edits] = by_edit.get(edits, 0) + 1
    detail = ", ".join(f"{k} {v}" for k, v in sorted(by_edit.items()))
    return (
        f"{len(report.rebaselined)} page(s) re-baselined"
        + (f" ({detail})" if detail else "")
        + f", {len(report.siblings_applied)} diverted update(s) applied, "
        f"{len(report.left)} drifted page(s) left for a person"
    )
