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
``reference_gate:``. Three more were found after (#487, #492): ``mnemo
regen-graph-edges`` rewriting the ``## Sources`` section, and reclassify's
keep and merge, which advanced the hash on a key built from the frontmatter
slug where the ledger keys the page by its file stem. This module holds both
halves of the fix:

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
from itertools import combinations
from pathlib import Path
from typing import Callable, Dict, FrozenSet, Iterator, List, Optional, Sequence, Tuple, Union

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
    #: Set by a caller that edited ``state`` entries itself (reclassify adds,
    #: dismisses and moves entries), so the state is saved on the way out.
    changed: bool = False

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
def edit_session(vault_root: Path, *, create: bool = False) -> Iterator[EditSession]:
    """Hold the extraction lock and yield an :class:`EditSession`.

    With *create*, a vault with no state file gets an empty state, for a
    caller that records entries of its own.

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
            if state_path.is_file():
                state = load_state(state_path)
            else:
                state = ExtractionState(last_run=None, entries={}) if create else None
        except StateSchemaError:
            state = None
        session = EditSession(vault_root=vault_root, state=state)
        try:
            yield session
        finally:
            if state is not None and (session.advanced or session.changed):
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


def absolutize_sources(
    text: str, prefix: str, only: Optional[FrozenSet[int]] = None,
) -> Optional[str]:
    """Undo the ``sources:`` relativization, with *prefix* as the vault path.

    ``promote.py`` rendered each project page's source as the scanner's
    absolute path, and the doctor fixer (``source_path_absolute``) rewrote it
    to ``bots/...``. Only items of the frontmatter's ``sources:`` block are
    touched. A prefix spelled with backslashes (a Windows vault) is rejoined
    with them, as ``str(Path)`` rendered it there.

    *only* restricts the undo to those ``bots/`` items (0-based, counted among
    the ``bots/`` items): the fixer swapped only the items that were absolute,
    so a page that mixed one absolute source among relative ones (#492,
    ``backup-by-risk-not-ritual``) is overshot by absolutizing every item.
    """
    bounds = _fm_bounds(text)
    if bounds is None:
        return None
    start, close = bounds
    sep = "\\" if ("\\" in prefix and "/" not in prefix) else "/"
    lines = text[start:close].split("\n")
    out: List[str] = []
    in_sources = changed = False
    seen = 0
    for line in lines:
        if line and not line[0].isspace():
            in_sources = line.rstrip() == "sources:"
        elif in_sources:
            m = _SOURCE_ITEM.match(line)
            if m and m.group(2).startswith("bots/"):
                if only is None or seen in only:
                    rel = m.group(2) if sep == "/" else m.group(2).replace("/", "\\")
                    line = f"{m.group(1)}{prefix.rstrip(sep)}{sep}{rel}{m.group(3)}"
                    changed = True
                seen += 1
        out.append(line)
    if not changed:
        return None
    return text[:start] + "\n".join(out) + text[close:]


def _relative_items(text: str) -> int:
    """How many ``sources:`` items are ``bots/``-relative."""
    bounds = _fm_bounds(text)
    if bounds is None:
        return 0
    n = 0
    in_sources = False
    for line in text[bounds[0]:bounds[1]].split("\n"):
        if line and not line[0].isspace():
            in_sources = line.rstrip() == "sources:"
        elif in_sources:
            m = _SOURCE_ITEM.match(line)
            n += bool(m and m.group(2).startswith("bots/"))
    return n


#: Past this many relative items only the whole block is tried: the subsets
#: grow as 2^n, and the one page #487 found mixed had two.
_SUBSET_LIMIT = 6


def _absolutizers(text: str, prefixes: Sequence[str]) -> List[Callable[[str], Optional[str]]]:
    n = _relative_items(text)
    if n == 0:
        return []
    subsets: List[Optional[FrozenSet[int]]] = [None]
    if 1 < n <= _SUBSET_LIMIT:
        subsets += [frozenset(c) for size in range(1, n)
                    for c in combinations(range(n), size)]
    return [lambda t, p=p, o=o: absolutize_sources(t, p, o)
            for p in prefixes for o in subsets]


def unkeep(text: str) -> Optional[str]:
    """Undo reclassify's keep (``reclassify_apply._rewrite_keep``): it set
    ``confidence: verified`` and appended an ``evidence:`` block to the
    frontmatter. Dropping both reproduced the pre-keep bytes on 24/24 kept
    pages of the maintainer's vault (#487)."""
    bounds = _fm_bounds(text)
    if bounds is None:
        return None
    start, close = bounds
    lines = text[start:close].split("\n")
    out: List[str] = []
    in_evidence = dropped_conf = dropped_ev = False
    for line in lines:
        if in_evidence and line.startswith("  "):
            continue
        in_evidence = False
        if line.startswith("evidence:"):
            in_evidence = dropped_ev = True
            continue
        if line.rstrip() == "confidence: verified" and not dropped_conf:
            dropped_conf = True
            continue
        out.append(line)
    if not (dropped_conf and dropped_ev):
        return None
    return text[:start] + "\n".join(out) + text[close:]


def _graph_section(links: Sequence[str]) -> str:
    """The ``## Sources`` section as the renderer and regen write it."""
    from mnemo.core.text_utils import GRAPH_SECTION_MARKER

    if not links:
        return ""
    body = "\n".join(f"- [[{link}]]" for link in links)
    return f"\n{GRAPH_SECTION_MARKER}\n## Sources\n{body}\n"


_PROJECT_PART = re.compile(r"^bots/[^/]+/(?=briefings/)")


def _link_spellings(sources: Sequence[str], prefixes: Sequence[str]) -> List[List[str]]:
    """Every spelling a page's section may have had for *sources*.

    The section is a pure function of the sources it was built from: the
    renderer copied each one through verbatim, often absolute; ``regen``
    relativizes it. Both strip ``.md``. The ledger's ``source_files`` may be
    either spelling of what the page was rendered with. Older extractions
    also cited a briefing as ``briefings/sessions/<id>`` (relative to its
    project) or ``mnemo://bots/...``; 36 of the pages #492 measured were
    rendered with one of those.
    """
    def strip_md(s: str) -> str:
        return s[:-3] if s.endswith(".md") else s

    def relative(s: str) -> str:
        for p in prefixes:
            for sep in ("/", "\\"):
                head = p.rstrip(sep) + sep
                if s.startswith(head) and s[len(head):].replace("\\", "/").startswith("bots/"):
                    return s[len(head):].replace("\\", "/")
        return s

    out: Dict[Tuple[str, ...], None] = {}
    out.setdefault(tuple(strip_md(s) for s in sources), None)
    out.setdefault(tuple(strip_md(relative(s)) for s in sources), None)
    def absolute(p: str, rel: str) -> str:
        # Joined as ``str(Path)`` rendered it: with backslashes on Windows.
        if "\\" in p and "/" not in p:
            return p.rstrip("\\") + "\\" + rel.replace("/", "\\")
        return f"{p.rstrip('/')}/{rel}"

    for p in prefixes:
        out.setdefault(tuple(
            strip_md(absolute(p, relative(s)) if relative(s).startswith("bots/") else s)
            for s in sources), None)
    out.setdefault(tuple(
        strip_md(f"mnemo://{relative(s)}" if relative(s).startswith("bots/") else s)
        for s in sources), None)
    out.setdefault(tuple(
        strip_md(_PROJECT_PART.sub("", relative(s), count=1)) for s in sources), None)
    return [list(links) for links in out]


def _regen_undoers(
    text: str, source_files: Sequence[str], prefixes: Sequence[str],
    vault_root: Optional[Path],
) -> List[Callable[[str], Optional[str]]]:
    """Undo ``mnemo regen-graph-edges`` (``_refresh_rule``).

    It replaced everything from the marker on with a section rebuilt from
    ``sources:``, after trimming the body's trailing newlines to one. What it
    replaced was either nothing or the section of the page as written, which
    the ledger's ``source_files`` rebuild (#487).

    Only a page that *is* regen's output is undone: the text must come back
    unchanged from regen's own function. A note a person appended after the
    section, or a link they edited in it, is not regen's, so the page is
    left; so is a page regen never ran on. Where regen did run, guessing what
    it replaced loses nothing — it had already replaced the whole region —
    and only a guess that reproduces the recorded hash byte for byte is taken.
    """
    from mnemo.cli.commands.regen_graph_edges import refreshed_rule
    from mnemo.core.text_utils import GRAPH_SECTION_MARKER

    if GRAPH_SECTION_MARKER not in text:
        return []
    root = Path(vault_root) if vault_root is not None else Path(prefixes[0] if prefixes else ".")
    # The page was rendered before later sources joined the ledger (a merge
    # appends, a re-extraction unions): its section cites a leading run of
    # them.
    sections = [""] + [_graph_section(links)
                       for n in range(len(source_files), 0, -1)
                       for links in _link_spellings(source_files[:n], prefixes)]

    def undo(t: str, section: str) -> Optional[str]:
        at = t.find(GRAPH_SECTION_MARKER)
        if at == -1 or refreshed_rule(t, root) != t:
            return None
        out = t[:at].rstrip("\n") + "\n" + section
        return out if out != t else None

    return [lambda t, sec=sec: undo(t, sec) for sec in dict.fromkeys(sections)]


def unmerge_sources(text: str, appended: Sequence[str], count: int) -> Optional[str]:
    """Undo reclassify's merge onto this page (``_append_sources``): drop the
    last *count* ``sources:`` items, each one a source a merge appended.

    It only appended items the block lacked, and created the block when there
    was none, so emptying the block drops its key too.
    """
    bounds = _fm_bounds(text)
    if bounds is None or count < 1:
        return None
    start, close = bounds
    lines = text[start:close].split("\n")
    try:
        idx = next(i for i, l in enumerate(lines) if l.startswith("sources:"))
    except StopIteration:
        return None
    end = idx + 1
    while end < len(lines) and lines[end].startswith("  - "):
        end += 1
    items = lines[idx + 1:end]
    if count > len(items):
        return None
    wanted = set(appended)
    if any(l[4:].strip() not in wanted for l in items[len(items) - count:]):
        return None
    keep = items[:len(items) - count]
    block = [lines[idx]] + keep if keep else []
    return text[:start] + "\n".join(lines[:idx] + block + lines[end:]) + text[close:]


def _unmergers(text: str, appended: Sequence[str]) -> List[Callable[[str], Optional[str]]]:
    if not appended:
        return []
    return [lambda t, k=k: unmerge_sources(t, appended, k)
            for k in range(1, len(appended) + 1)]


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


#: Display order of the edit names in :func:`explain_drift`'s answer.
_KINDS = ("sources", "reference_gate", "slug", "regen", "keep", "merge")
#: The order edits are undone in: last written first. Extraction wrote the
#: page; reclassify's keep; the #114 slug stamp; the doctor fixer's sources
#: swap; ``regen-graph-edges`` (09-08); dedupe-audit merges (09-22); the
#: ``reference_gate`` stamp (09-23).
_UNDO_ORDER = ("reference_gate", "merge", "regen", "sources", "slug", "keep")


def _reversals(
    text: str, prefixes: Sequence[str], source_files: Sequence[str],
    merged_sources: Sequence[str], vault_root: Optional[Path],
) -> Dict[str, List[Callable[[str], Optional[str]]]]:
    return {
        "sources": _absolutizers(text, prefixes),
        "reference_gate": [unstamp_reference_gate],
        "slug": [unstamp_slug],
        "regen": _regen_undoers(text, source_files, prefixes, vault_root),
        "keep": [unkeep],
        "merge": _unmergers(text, merged_sources),
    }


def explain_drift(
    text: str, written_hash: str, prefixes: Sequence[str],
    source_files: Sequence[str] = (), merged_sources: Sequence[str] = (),
    vault_root: Optional[Path] = None,
) -> Optional[str]:
    """Name the known machine edits that account for *text*'s drift, or None.

    The known edits are undone, in every combination, newest first, and each
    result hashed; a match means the only changes since the extractor wrote
    the page were those edits. The name is ``+``-joined, e.g.
    ``"sources+reference_gate"``; of several matches the one naming fewest
    edits wins. A reworded body, a hand-added line — anything else fails
    every candidate.

    *source_files* are the ledger's (the ``regen`` section is rebuilt from
    them), *merged_sources* the sources reclassify merges appended to this
    page (:func:`merge_appends`).
    """
    fns = _reversals(text, prefixes, source_files, merged_sources, vault_root)
    best: Optional[Tuple[str, ...]] = None
    stack: List[Tuple[int, str, Tuple[str, ...]]] = [(0, text, ())]
    seen = set()
    while stack:
        depth, candidate, done = stack.pop()
        if (depth, candidate) in seen:
            continue
        seen.add((depth, candidate))
        if done and content_hash(candidate) == written_hash:
            if best is None or len(done) < len(best):
                best = done
            continue
        if depth == len(_UNDO_ORDER):
            continue
        kind = _UNDO_ORDER[depth]
        stack.append((depth + 1, candidate, done))
        for fn in fns[kind]:
            undone = fn(candidate)
            if undone is not None and undone != candidate:
                stack.append((depth + 1, undone, done + (kind,)))
    if best is None:
        return None
    return "+".join(k for k in _KINDS if k in best)


def merge_appends(vault_root: Path) -> Dict[str, List[str]]:
    """Ledger key -> the sources reclassify merges appended to that page.

    Read off every ``shared/_archive/reclassify-*/`` run: its manifest names
    each merge's target, and ``merged/<slug>.md`` is the merged-away page,
    whose ``sources:`` are what ``_append_sources`` added (#487).
    """
    import json

    from mnemo.core.reclassify_types import split_frontmatter

    vault_root = Path(vault_root)
    out: Dict[str, List[str]] = {}
    for manifest in sorted((vault_root / "shared" / "_archive").glob("reclassify-*/manifest.json")):
        try:
            moves = json.loads(manifest.read_text(encoding="utf-8")).get("moves") or []
        except (OSError, ValueError, AttributeError):
            continue
        for move in moves:
            if not isinstance(move, dict) or move.get("verdict") != "merge":
                continue
            target = move.get("target_path")
            if not target:
                continue
            key = page_key(vault_root / str(target), vault_root)
            try:
                merged = (manifest.parent / "merged" / f"{move.get('slug')}.md").read_text(encoding="utf-8")
            except OSError:
                continue
            sources = split_frontmatter(merged)[0].get("sources") or []
            if not isinstance(sources, list):
                sources = [sources]
            bucket = out.setdefault(key, []) if key else None
            if bucket is not None:
                bucket.extend(str(s) for s in sources if str(s) not in bucket)
    return out


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
    appends = merge_appends(vault_root) if drifted else {}
    for key, entry, pages in drifted:
        for page, text in pages:
            edits = explain_drift(text, entry.written_hash, prefixes,
                                  entry.source_files, appends.get(key, ()), vault_root)
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
