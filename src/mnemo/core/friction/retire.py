"""Retirement — a contradicted rule stops competing, and nothing else changes.

The ledger records that a user correction contradicts a rule. This module is
where the vault first acts on that: the contradicted page gains three
frontmatter keys naming what replaced it, the replacement gains a
``supersedes`` list, and every automatic surface stops offering the old rule.
It is the only writer in the friction package, and those four keys are the
only thing it ever writes — no body edit, no deletion, no archive move.

Retired means *not a candidate*, never *gone*:

- the reflex index marks the page (``docs[slug]["retired"]``) and
  ``reflex.decide.candidates_for_project`` drops it, so injection, ``replay``
  and ``mnemo why`` all lose it from that one filter;
- ``list_rules_by_topic`` withholds it and says how many it withheld;
- ``read_mnemo_rule`` by slug always returns it, retirement stated first;
- ``existing_rules_fragment`` keeps listing it, marked, so the extractor does
  not mint the rule the user just contradicted again (#184).

**A retirement is honoured only when the ledger backs it** — see
:func:`mnemo.core.filters.is_retired`. Writing one for a record that is not in
the ledger would leave keys nothing honours, so :func:`retire` refuses it.

Four guards, each a refusal that names the slugs involved rather than a
silent skip:

- no replacement page → refused: a vault that removes a rule and puts nothing
  in its place has lost knowledge;
- a cycle → refused, the chain walked first the way ``mnemo land`` refuses a
  cyclic contract. A → B then later B → C is a chain and is allowed; A → B
  when B already (transitively) retired into A is not;
- a page already retired by a different correction → refused; re-pointing it
  would rewrite history the ledger still describes;
- more than :data:`MAX_RETIREMENTS_PER_RUN` in one run → nothing retired, the
  overflow reported. A circuit breaker for a malformed contradiction pass that
  names many slugs at once, not a judgement (#329's per-pass shape).

**Inert by default.** Automatic retirement during extraction runs only when
``friction.autoRetire`` is ``true`` in config (#234's opt-in shape); see
:func:`auto_retire`. A human-run ``--apply`` of a reviewed plan is not
automatic and is not gated by it.

Nothing here raises into a caller: a failed write is one ``.errors.log`` row
and a refused :class:`RetireResult`. The ledger record always stands.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from mnemo.core import corrections, errors
from mnemo.core.filters import (
    SUPERSEDED_AT,
    SUPERSEDED_BY,
    SUPERSEDED_BY_FRICTION,
    SUPERSEDES,
    derive_rule_slug,
    is_proposed_sibling,
    is_retired,
    parse_frontmatter,
)
from mnemo.core.friction.ledger import (
    LINK_NONE,
    FrictionRecord,
    iter_records,
    ledger_path,
    record_id,
)

#: At most this many rules retire in one run. Above it, none do.
MAX_RETIREMENTS_PER_RUN = 5

#: ``friction.autoRetire`` in ``mnemo.config.json``. Absent means off.
AUTO_RETIRE_KEY = "autoRetire"
AUTO_RETIRE_DEFAULT = False

#: :attr:`RetirePlan.status` values.
PLAN_RETIRE = "retire"  # would be written by --apply
PLAN_DONE = "done"  # this record already retired this page
PLAN_MISSING = "missing"  # the contradicted slug has no live page
PLAN_REFUSED = "refused"  # a guard said no; ``reason`` says which

_PAGE_TYPES = ("feedback", "user", "reference", "project")
_ERROR_WHERE = "friction.retire"
_RETIREMENT_KEYS = (SUPERSEDED_BY, SUPERSEDED_AT, SUPERSEDED_BY_FRICTION)


@dataclass(frozen=True)
class RetireResult:
    """What one :func:`retire` or :func:`undo` call did.

    ``ok`` is False exactly when nothing was written. ``slugs`` are the pages
    retired (or restored, for undo); ``written`` their vault-relative paths
    plus the replacement's.
    """

    ok: bool
    action: str  # "retired" | "already" | "undone" | "refused"
    friction_id: str
    slugs: list[str] = field(default_factory=list)
    replacement: str | None = None
    reason: str = ""
    written: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RetirePlan:
    """One (correction, contradicted rule) pair and what ``--apply`` would do."""

    friction_id: str
    target: str
    replacement: str | None
    status: str
    quote: str
    link_basis: str
    ts: str = ""
    reason: str = ""

    def line(self) -> str:
        """Two lines for the dry run: the action, then the quote behind it."""
        arrow = f"{self.target} → {self.replacement or '(no replacement)'}"
        head = f"{self.status:<8} {arrow}  [{self.friction_id}, {self.link_basis}]"
        if self.reason:
            head += f"  — {self.reason}"
        return f"{head}\n         \"{self.quote}\""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class RetireRun:
    """One capped application of a plan."""

    results: list[RetireResult] = field(default_factory=list)
    #: How many retirements the run held when the cap tripped; 0 otherwise.
    overflow: int = 0
    #: Set when the run did nothing on purpose (cap tripped, autoRetire off).
    skipped: str = ""

    @property
    def retired(self) -> list[RetireResult]:
        return [r for r in self.results if r.ok and r.action == "retired"]


# --- the ledger, as the predicate sees it ----------------------------------

_LEDGER_CACHE: dict[str, tuple[tuple, dict[str, frozenset[str]]]] = {}


def _ledger_stamp(path: Path) -> tuple:
    stamp = []
    for p in (path.with_name(path.name + ".1"), path):
        try:
            st = p.stat()
            stamp.append((st.st_mtime_ns, st.st_size))
        except OSError:
            stamp.append(None)
    return tuple(stamp)


def ledger_contradictions(vault_root: Path) -> dict[str, frozenset[str]]:
    """``{friction id: slugs it contradicts}`` for every linked ledger record.

    A record whose ``link_basis`` is ``none`` linked nothing and cannot back a
    retirement, so it is left out. Cached on the ledger files' mtime and size:
    the reflex index calls :func:`is_retired` once per retired page while it
    builds, and the ledger only changes when a row is appended.
    """
    path = ledger_path(Path(vault_root))
    stamp = _ledger_stamp(path)
    key = str(path)
    hit = _LEDGER_CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    out: dict[str, set[str]] = {}
    for rec in iter_records(vault_root):
        if rec.link_basis == LINK_NONE or not rec.contradicts:
            continue
        out.setdefault(_id_of(rec), set()).update(rec.contradicts)
    frozen = {k: frozenset(v) for k, v in out.items()}
    _LEDGER_CACHE[key] = (stamp, frozen)
    return frozen


def _id_of(rec: FrictionRecord) -> str:
    return rec.id or record_id(rec)


# --- pages -----------------------------------------------------------------


@dataclass
class _Page:
    path: Path
    slug: str
    fm: dict
    text: str


def _live_pages(vault_root: Path) -> Iterable[_Page]:
    """Every live rule page, keyed the way the reflex index keys it."""
    for page_type in _PAGE_TYPES:
        type_dir = vault_root / "shared" / page_type
        if not type_dir.is_dir():
            continue
        for md in sorted(type_dir.glob("*.md")):
            if is_proposed_sibling(md):
                continue
            text = _read(md)
            if text is None:
                continue
            fm = parse_frontmatter(text)
            yield _Page(md, derive_rule_slug(fm, md.stem), fm, text)


def _pages_by_slug(vault_root: Path) -> dict[str, _Page]:
    out: dict[str, _Page] = {}
    for page in _live_pages(vault_root):
        out.setdefault(page.slug, page)
    return out


def _read(path: Path) -> str | None:
    # Bytes, not read_text: universal-newline decoding would turn a CRLF page
    # into an LF one on write, changing more than the keys this module owns.
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _rel(vault_root: Path, path: Path) -> str:
    try:
        return path.relative_to(vault_root).as_posix()
    except ValueError:
        return str(path)


# --- frontmatter surgery ---------------------------------------------------
#
# Line-level, on the raw block, so every key this module does not own keeps
# its bytes. ``parse_frontmatter`` is the reader of record; these helpers only
# ever touch the four keys above.

_KEY_RE = re.compile(r"^([^\s:#][^:]*):")


def _split(text: str) -> tuple[list[str], str] | None:
    """The frontmatter lines and the rest, for an LF **or** CRLF page.

    ``_read`` keeps a CRLF page's bytes on purpose, so every line here can end
    in ``\r``. The terminator is matched both ways and the ``\r`` is stripped
    from the returned lines, which is what the key helpers below expect; the
    remainder is handed back untouched, so a rewritten page keeps the line
    endings it arrived with.
    """
    if text.startswith("---\n"):
        start = 4
    elif text.startswith("---\r\n"):
        start = 5
    else:
        return None
    for term in ("\n---\n", "\n---\r\n"):
        end = text.find(term, start)
        if end != -1:
            break
    if end == -1:
        return None
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in text[start:end].split("\n")]
    # ``end`` points at the newline before the closing ``---``. Hand the tail
    # back without it so the caller re-joins with the page's own terminator.
    return lines, text[end + 1:]


def _key_spans(lines: list[str]) -> dict[str, tuple[int, int]]:
    """``{key: (first line, one past its last continuation line)}``."""
    spans: dict[str, tuple[int, int]] = {}
    current: str | None = None
    for i, line in enumerate(lines):
        m = _KEY_RE.match(line)
        if m is not None:
            current = m.group(1).strip()
            spans.setdefault(current, (i, i + 1))
            continue
        if current is not None and line.strip() and line.startswith((" ", "\t", "- ")):
            first, _last = spans[current]
            if _last == i:
                spans[current] = (first, i + 1)
            continue
        current = None
    return spans


def _set_block(lines: list[str], key: str, block: list[str]) -> list[str]:
    """Replace ``key``'s lines with ``block`` in place; append when absent.

    An empty ``block`` removes the key. In place, so a retire followed by an
    undo gives back the page byte for byte.
    """
    span = _key_spans(lines).get(key)
    if span is not None:
        return lines[:span[0]] + block + lines[span[1]:]
    if not block:
        return lines
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    return lines[:end] + block + lines[end:]


def _with_keys(text: str, scalars: dict[str, str], lists: dict[str, list[str]],
               drop: Iterable[str] = ()) -> str:
    from mnemo.core.extract.inbox.rendering import _yaml_scalar

    split = _split(text)
    if split is None:
        raise ValueError("page has no frontmatter block this module can edit")
    lines, tail = split
    for key in drop:
        lines = _set_block(lines, key, [])
    for key, value in scalars.items():
        lines = _set_block(lines, key, [f"{key}: {_yaml_scalar(value)}"])
    for key, items in lists.items():
        block = [f"{key}:"] + [f"  - {_yaml_scalar(i)}" for i in items] if items else []
        lines = _set_block(lines, key, block)
    # Re-join with the page's own terminator: ``tail`` still carries it, so a
    # CRLF page stays CRLF rather than getting an LF frontmatter on a CRLF body.
    nl = "\r\n" if tail.startswith("---\r\n") else "\n"
    return "---" + nl + nl.join(lines) + nl + tail


def _supersedes(fm: dict) -> list[str]:
    raw = fm.get(SUPERSEDES)
    if isinstance(raw, str):
        raw = [raw] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    return [s for s in raw if isinstance(s, str) and s.strip()]


def _write_all(vault_root: Path, edits: list[tuple[Path, str, str]]) -> list[str]:
    """Write every ``(path, old, new)``; on any failure put them all back."""
    from mnemo.core.atomic import atomic_write_bytes

    done: list[tuple[Path, str]] = []
    try:
        for path, old, new in edits:
            atomic_write_bytes(path, new.encode("utf-8"))
            done.append((path, old))
    except Exception:
        for path, old in reversed(done):
            try:
                atomic_write_bytes(path, old.encode("utf-8"))
            except Exception:
                pass
        raise
    return [_rel(vault_root, p) for p, _o, _n in edits]


def _reindex(vault_root: Path) -> None:
    """Rebuild the reflex index so a retirement takes effect before next session.

    The index is where injection learns a rule is retired; a stale one would
    keep offering it until the next SessionStart rebuild. Best-effort, like
    extraction's own rebuild.
    """
    try:
        from mnemo.core.reflex import index as reflex_index

        reflex_index.write_index(vault_root, reflex_index.build_index(vault_root))
    except Exception as exc:  # noqa: BLE001 — a retirement stands without it
        _log(vault_root, exc, "reindex")


def _log(vault_root: Path, exc: BaseException, what: str) -> None:
    try:
        errors.log_error(Path(vault_root), f"{_ERROR_WHERE}.{what}", exc)
    except Exception:
        pass


# --- the chain -------------------------------------------------------------


def _chain_from(start: str, edges: dict[str, str]) -> list[str]:
    """``start`` then every slug its ``superseded_by`` leads to, loop-safe."""
    chain = [start]
    seen = {start}
    cur = start
    while cur in edges:
        cur = edges[cur]
        chain.append(cur)
        if cur in seen:
            break
        seen.add(cur)
    return chain


def _edges(pages: dict[str, _Page]) -> dict[str, str]:
    # Raw keys, honoured or not: a cycle guard that trusted only the ledger
    # would let a hand-edited key close a loop the moment its record appeared.
    out = {}
    for slug, page in pages.items():
        nxt = page.fm.get(SUPERSEDED_BY)
        if isinstance(nxt, str) and nxt.strip():
            out[slug] = nxt.strip()
    return out


def _guard(target: str, replacement: str | None, friction_id: str,
           pages: dict[str, _Page], edges: dict[str, str]) -> tuple[str, str]:
    """``(status, reason)`` for retiring ``target`` into ``replacement``."""
    page = pages.get(target)
    if page is None:
        return PLAN_MISSING, f"no live page for {target}"
    current = edges.get(target)
    if current is not None:
        by = page.fm.get(SUPERSEDED_BY_FRICTION) or "no friction id"
        if by == friction_id and current == replacement:
            return PLAN_DONE, ""
        return PLAN_REFUSED, f"{target} is already superseded by {current} ({by})"
    if not replacement:
        return PLAN_REFUSED, (
            f"no replacement page for {target}: retiring it would leave "
            "nothing in its place"
        )
    if replacement == target:
        return PLAN_REFUSED, f"{target} cannot supersede itself"
    if replacement not in pages:
        return PLAN_REFUSED, f"replacement {replacement} for {target} has no live page"
    chain = _chain_from(replacement, edges)
    if target in chain:
        path = " → ".join(chain[: chain.index(target) + 1])
        return PLAN_REFUSED, (
            f"cycle: {replacement} already leads to {target} ({path}); "
            f"{target} → {replacement} would close it"
        )
    return PLAN_RETIRE, ""


# --- the writer ------------------------------------------------------------


def retire(vault_root: Path, rec: FrictionRecord, *, replacement: str | None) -> RetireResult:
    """Retire every slug ``rec`` contradicts into ``replacement``.

    All or nothing: every target is checked (live page, not already retired
    elsewhere, no cycle) before anything is written, and a failed write puts
    back the pages already written. Retiring a page this record already
    retired into the same replacement is ``ok`` with action ``already`` and
    writes nothing.

    Refused when ``rec`` is not a linked record in the ledger — the keys it
    would write could never be honoured by :func:`is_retired`.
    """
    result = _retire(Path(vault_root), rec, replacement)
    if result.ok and result.written:
        _reindex(Path(vault_root))
    return result


def _retire(vault_root: Path, rec: FrictionRecord, replacement: str | None) -> RetireResult:
    fid = _id_of(rec)
    targets = list(dict.fromkeys(s for s in rec.contradicts if s))

    def refused(reason: str) -> RetireResult:
        return RetireResult(False, "refused", fid, targets, replacement, reason)

    try:
        if rec.link_basis == LINK_NONE or not targets:
            return refused(f"{fid} links no rule")
        if fid not in ledger_contradictions(vault_root):
            return refused(
                f"{fid} is not a linked record in the friction ledger; "
                "a retirement it cannot trace would never be honoured"
            )
        pages = _pages_by_slug(vault_root)
        edges = _edges(pages)
        pending: list[str] = []
        for target in targets:
            status, reason = _guard(target, replacement, fid, pages, edges)
            if status == PLAN_DONE:
                continue
            if status != PLAN_RETIRE:
                return refused(reason)
            pending.append(target)
        if not pending:
            return RetireResult(True, "already", fid, targets, replacement)

        assert replacement is not None  # _guard refuses a missing one
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        edits: list[tuple[Path, str, str]] = []
        for target in pending:
            page = pages[target]
            new = _with_keys(page.text, {
                SUPERSEDED_BY: replacement,
                SUPERSEDED_AT: stamp,
                SUPERSEDED_BY_FRICTION: fid,
            }, {})
            edits.append((page.path, page.text, new))
        rep = pages[replacement]
        have = _supersedes(rep.fm)
        wanted = have + [t for t in pending if t not in have]
        if wanted != have:
            edits.append((rep.path, rep.text, _with_keys(rep.text, {}, {SUPERSEDES: wanted})))
        written = _write_all(vault_root, edits)
        return RetireResult(True, "retired", fid, pending, replacement, "", written)
    except Exception as exc:  # never raises — the ledger record still stands
        _log(vault_root, exc, "write")
        return refused(f"write failed: {exc}")


def undo(vault_root: Path, friction_id: str) -> RetireResult:
    """Restore every page ``friction_id`` retired, both sides.

    Removes the three retirement keys from each page that names
    ``friction_id``, and removes each such page from its replacement's
    ``supersedes`` (dropping the key when it empties). Does not consult the
    ledger: a retirement whose record went missing must still be undoable.
    """
    vault_root = Path(vault_root)
    fid = (friction_id or "").strip()
    try:
        pages = _pages_by_slug(vault_root)
        retired = [
            p for p in pages.values()
            if str(p.fm.get(SUPERSEDED_BY_FRICTION) or "").strip() == fid
        ]
        if not fid or not retired:
            return RetireResult(False, "refused", fid, [], None,
                                f"no page carries retirement {fid or '(empty id)'}")
        edits: dict[Path, tuple[str, str]] = {}
        replacements: list[str] = []
        for page in retired:
            edits[page.path] = (page.text, _with_keys(page.text, {}, {}, drop=_RETIREMENT_KEYS))
            rep = str(page.fm.get(SUPERSEDED_BY) or "").strip()
            if rep and rep not in replacements:
                replacements.append(rep)
        slugs = [p.slug for p in retired]
        for rep_slug in replacements:
            rep = pages.get(rep_slug)
            if rep is None:
                continue
            have = _supersedes(rep.fm)
            kept = [s for s in have if s not in slugs]
            if kept == have:
                continue
            orig, cur = edits.get(rep.path, (rep.text, rep.text))
            edits[rep.path] = (orig, _with_keys(cur, {}, {SUPERSEDES: kept}))
        written = _write_all(vault_root, [(p, o, n) for p, (o, n) in edits.items()])
        _reindex(vault_root)
        return RetireResult(True, "undone", fid, slugs,
                            replacements[0] if len(replacements) == 1 else None,
                            "", written)
    except Exception as exc:
        _log(vault_root, exc, "undo")
        return RetireResult(False, "refused", fid, [], None, f"undo failed: {exc}")


# --- the plan --------------------------------------------------------------


def find_replacement(vault_root: Path, rec: FrictionRecord,
                     pages: dict[str, _Page] | None = None) -> str | None:
    """The live page written from ``rec``'s correction, or ``None``.

    A page is the replacement when its ``evidence.quote`` is the record's
    quote (normalised; either may be an excerpt of the other). When several
    match, the one whose ``evidence.source`` is the record's briefing wins,
    then the lowest slug. Nothing fuzzier: a guessed replacement would retire
    a rule into a page that does not say its opposite.
    """
    pages = _pages_by_slug(Path(vault_root)) if pages is None else pages
    quote = corrections.normalize(rec.quote)
    if not quote:
        return None
    contradicted = set(rec.contradicts)
    hits: list[tuple[int, str]] = []
    for slug, page in pages.items():
        if slug in contradicted:
            continue
        evidence = page.fm.get("evidence")
        if not isinstance(evidence, dict):
            continue
        theirs = corrections.normalize(str(evidence.get("quote") or ""))
        if not theirs or not (quote in theirs or theirs in quote):
            continue
        same_source = bool(rec.briefing) and str(evidence.get("source") or "") == rec.briefing
        hits.append((0 if same_source else 1, slug))
    return min(hits)[1] if hits else None


def plan_retirements(vault_root: Path, *, since: date | str | None = None) -> list[RetirePlan]:
    """Every retirement the ledger calls for, oldest record first.

    What ``mnemo friction --retire`` prints in dry run: one plan per
    (record, contradicted slug), each carrying the quote that justifies it.
    Nothing is written. Plans are judged in order against the vault *as the
    earlier plans would leave it*, so two records that would retire the same
    page, or close a cycle between them, are caught here and not at apply.
    """
    return _plan(Path(vault_root), iter_records(vault_root, since=since))


def _plan(vault_root: Path, records: Iterable[FrictionRecord]) -> list[RetirePlan]:
    pages = _pages_by_slug(vault_root)
    edges = _edges(pages)
    plans: list[RetirePlan] = []
    for rec in records:
        if rec.link_basis == LINK_NONE or not rec.contradicts:
            continue
        fid = _id_of(rec)
        replacement = find_replacement(vault_root, rec, pages)
        for target in dict.fromkeys(rec.contradicts):
            status, reason = _guard(target, replacement, fid, pages, edges)
            if status == PLAN_RETIRE:
                edges[target] = replacement  # later plans see this one applied
            plans.append(RetirePlan(
                friction_id=fid, target=target, replacement=replacement,
                status=status, quote=rec.quote, link_basis=rec.link_basis,
                ts=rec.ts, reason=reason,
            ))
    return plans


def apply_retirements(vault_root: Path, plans: list[RetirePlan], *,
                      cap: int = MAX_RETIREMENTS_PER_RUN) -> RetireRun:
    """Execute exactly the ``retire`` lines of ``plans``, under the cap.

    More than ``cap`` pending retirements → nothing is written and
    :attr:`RetireRun.overflow` says how many there were. Otherwise each plan
    is re-checked against the vault as it is now (it may have changed since
    the dry run) and written; a line that no longer passes is refused, not
    forced.
    """
    vault_root = Path(vault_root)
    pending = [p for p in plans if p.status == PLAN_RETIRE]
    if len(pending) > cap:
        return RetireRun(overflow=len(pending), skipped=(
            f"{len(pending)} retirements exceed the per-run cap of {cap}; "
            "nothing retired"
        ))
    records = {_id_of(r): r for r in iter_records(vault_root)}
    results: list[RetireResult] = []
    for plan in pending:
        rec = records.get(plan.friction_id)
        if rec is None:
            results.append(RetireResult(False, "refused", plan.friction_id, [plan.target],
                                        plan.replacement,
                                        f"{plan.friction_id} is no longer in the ledger"))
            continue
        one = dataclasses.replace(rec, contradicts=[plan.target])
        results.append(_retire(vault_root, one, plan.replacement))
    if any(r.ok and r.written for r in results):
        _reindex(vault_root)
    return RetireRun(results=results)


def auto_retire_enabled(cfg: dict | None = None) -> bool:
    """``friction.autoRetire`` from config; ``False`` when absent or unreadable."""
    try:
        if cfg is None:
            from mnemo.core.config import load_config

            cfg = load_config()
        section = cfg.get("friction") or {}
        return section.get(AUTO_RETIRE_KEY, AUTO_RETIRE_DEFAULT) is True
    except Exception:
        return AUTO_RETIRE_DEFAULT


def auto_retire(vault_root: Path, records: Iterable[FrictionRecord], *,
                cfg: dict | None = None) -> RetireRun:
    """The extraction path's entry: retire what ``records`` call for, if allowed.

    Does nothing — no plan, no read of the vault — unless
    ``friction.autoRetire`` is ``true``. That default is what lets this
    subsystem land inert: ``replay``'s historical counts cannot move until a
    human has read the dry run and switched it on.
    """
    if not auto_retire_enabled(cfg):
        return RetireRun(skipped="friction.autoRetire is off")
    try:
        return apply_retirements(Path(vault_root), _plan(Path(vault_root), records))
    except Exception as exc:  # never raises into extraction
        _log(Path(vault_root), exc, "auto")
        return RetireRun(skipped=f"auto-retire failed: {exc}")


__all__ = [
    "AUTO_RETIRE_DEFAULT",
    "AUTO_RETIRE_KEY",
    "MAX_RETIREMENTS_PER_RUN",
    "PLAN_DONE",
    "PLAN_MISSING",
    "PLAN_REFUSED",
    "PLAN_RETIRE",
    "RetirePlan",
    "RetireResult",
    "RetireRun",
    "apply_retirements",
    "auto_retire",
    "auto_retire_enabled",
    "find_replacement",
    "is_retired",
    "ledger_contradictions",
    "plan_retirements",
    "retire",
    "undo",
]
