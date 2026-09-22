"""The portable rule page: what ``mnemo publish`` writes and ``mnemo import`` reads.

A vault rule page is one person's: its ``sources:`` are paths inside that
vault, its ``enforce:`` block can block a tool call on that machine, its
``last_sync`` is the extractor's bookkeeping. The *portable* page is the
same markdown with all of that removed and one thing added — a ``published:``
block naming the vault it came from — so a second vault can stage it for
review without ever mistaking it for something its own user said.

Three shapes, two directions:

* vault page ``--to_portable-->`` portable page (``publish``)
* portable page ``--from_portable-->`` :class:`PortableRule` (``import``)
* :class:`PortableRule` ``--to_vault_page-->`` staged page for ``shared/_inbox/``

The tree in the repo is ``<repo>/.mnemo-shared/<type>/<slug>.md`` —
:data:`SHARE_DIR` — outside ``.mnemo/`` and ``.claude/`` because
``mnemo init --project`` gitignores both.

Frontmatter is read with :func:`mnemo.core.filters.parse_frontmatter` (one
nesting level) and written with the same ``_yaml_scalar`` quoting
``extract/inbox/rendering._render_page`` uses, so every existing reader of
``shared/`` parses what this module writes. Pure text in, text out; the one
file this module owns is ``<vault>/.mnemo/vault-id``.

Identity across vaults
----------------------

A vault has no identity anywhere else in mnemo. :func:`vault_id` creates one
on first use: an opaque random token, never a hostname, a path, a git
identity or an email. It exists so a re-publish prunes only its own files
and an import skips the files that came from the importing vault. A rule
that hops — imported into vault B, reviewed, promoted, published again by
B — still names vault A: :func:`to_portable` carries the ``imported:``
block's provenance through unchanged instead of restamping.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.extract.inbox.rendering import _render_nested_block, _yaml_scalar
from mnemo.core.filters import derive_rule_slug, parse_frontmatter, topic_tags
from mnemo.core.redact import redact_secrets
from mnemo.core.text_utils import retrieval_body

#: Root of the published tree, relative to the repo. Not ``.mnemo/`` and not
#: ``.claude/``: ``mnemo init --project`` appends both to ``.gitignore`` and
#: gitignore cannot re-include a path under an excluded directory.
SHARE_DIR = ".mnemo-shared"

#: The ``origin:`` value on a staged page that came from another vault.
#: Sits beside ``origin: backfill`` (``core/backfill/origin.py``); the two
#: never collide because each predicate compares to its own literal.
IMPORTED = "imported"

#: Frontmatter key of the provenance block on a portable page.
PUBLISHED_KEY = "published"

#: Frontmatter key of the provenance block on a staged (imported) page.
IMPORTED_KEY = "imported"

#: ``<vault>/.mnemo/vault-id``, relative to the vault root.
VAULT_ID_REL = ".mnemo/vault-id"

#: What an imported rule's ``confidence`` becomes when it was published
#: ``verified``. Every "you said" surface compares ``== "verified"``, so this
#: value excludes the rule from them with no further change.
VERIFIED_ELSEWHERE = "verified-elsewhere"

# Vault timestamps, in the order they are consulted for a page's date.
_VAULT_TIMESTAMPS = ("last_sync", "promoted_at", "extraction_run", "extracted_at")


@dataclass(frozen=True)
class PortableRule:
    """One rule as read back from a portable page.

    ``confidence`` is the value *as published* (``verified`` or ``inferred``),
    not what the importing vault will stamp — :func:`to_vault_page` does that
    translation. ``hash`` is :func:`portable_hash` of the page text this was
    read from, the identity both the publish manifest and the import ledger
    record.
    """

    slug: str
    type: str
    name: str
    description: str
    body: str
    tags: Tuple[str, ...]
    confidence: str
    stability: str
    quote: Optional[str]
    evidence_source: Optional[str]
    vault: str
    project: str
    published_at: str
    source_count: int
    hash: str


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _split_page(text: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """Frontmatter dict and body, or ``({}, None)`` when there is no frontmatter."""
    if not text.startswith("---\n"):
        return {}, None
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, None
    return parse_frontmatter(text), text[end + len("\n---\n"):]


def _clean_body(body: str) -> str:
    """The rule text alone: graph section and mnemo's advisory notes gone,
    one trailing newline."""
    cleaned = retrieval_body(body).strip("\n")
    return cleaned + "\n" if cleaned else ""


def _iso_date(value: object) -> str:
    """``YYYY-MM-DD`` from a date, a datetime, or an ISO string with a time."""
    return str(value)[:10]


def _page_date(fm: Dict[str, Any], today: object) -> str:
    """When the vault last wrote this page, or *today* if it never said.

    The extractor stamps every page it writes (``extracted_at``,
    ``extraction_run``, ``last_sync``; ``promoted_at`` for project pages)
    and re-stamps on every rewrite, so the newest of those is the date the
    rule took its current form. A hand-written page carries none and gets
    *today*.
    """
    dates = [
        _iso_date(fm[key]) for key in _VAULT_TIMESTAMPS
        if isinstance(fm.get(key), str) and fm.get(key)
    ]
    return max(dates) if dates else _iso_date(today)


def _evidence(fm: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    ev = fm.get("evidence")
    if not isinstance(ev, dict):
        return None, None
    quote = ev.get("quote")
    source = ev.get("source")
    return (
        str(quote) if isinstance(quote, str) and quote else None,
        str(source) if isinstance(source, str) and source else None,
    )


def _evidence_block(quote: Optional[str], source: Optional[str]) -> str:
    if not quote:
        return ""
    data: Dict[str, Any] = {"quote": quote}
    if source:
        data["source"] = source
    return _render_nested_block("evidence", data)


def _block_list(key: str, items: List[str]) -> str:
    """A top-level block list. ``filters.parse_frontmatter`` reads inline
    lists only under a nested key, so top-level lists are always block-style
    (``sources: []`` for the empty case is the one inline spelling it reads)."""
    if not items:
        return f"{key}: []\n"
    return f"{key}:\n" + "".join(f"  - {_yaml_scalar(i)}\n" for i in items)


def _int(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _provenance(block: object) -> Optional[Dict[str, Any]]:
    """The four provenance fields out of a ``published:`` / ``imported:``
    block, or ``None`` when the block does not name a vault."""
    if not isinstance(block, dict):
        return None
    vault = block.get("vault")
    if not isinstance(vault, str) or not vault.strip():
        return None
    return {
        "vault": vault.strip(),
        "project": str(block.get("project") or ""),
        "date": _iso_date(block.get("date") or ""),
        "source_count": _int(block.get("source_count")),
    }


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------


def is_imported_frontmatter(fm: Any) -> bool:
    """True when parsed frontmatter carries ``origin: imported``.

    Same tolerance as ``backfill.origin.is_backfill_frontmatter``: the
    top-level spelling (what :func:`to_vault_page` writes and what the flat
    ``scanner.parse_frontmatter`` yields) or the nested ``metadata: {origin:
    imported}`` spelling (what the nesting-aware parser would yield for a
    memory-file shape). Either parser, same answer.
    """
    if not isinstance(fm, dict):
        return False
    if str(fm.get("origin") or "") == IMPORTED:
        return True
    metadata = fm.get("metadata")
    if isinstance(metadata, dict) and str(metadata.get("origin") or "") == IMPORTED:
        return True
    return False


def vault_id(vault_root: Union[str, Path]) -> str:
    """This vault's opaque id, created on first call at ``<vault>/.mnemo/vault-id``.

    A random token; never derived from the machine, the path, or git, so it
    carries no PII into a tree that gets committed. A blank or missing file
    gets a fresh id.
    """
    path = Path(vault_root) / VAULT_ID_REL
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing
    fresh = secrets.token_hex(16)
    atomic_write_bytes(path, (fresh + "\n").encode("utf-8"))
    return fresh


def portable_hash(text: str) -> str:
    """Identity of a portable page: sha256 of its text with line endings
    normalised to ``\\n``.

    Both ``publish`` (manifest) and ``import`` (ledger) record this and
    compare it, so it has one definition. Normalising means a tree checked
    out with CRLF hashes the same as the LF bytes the publisher wrote.
    """
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def to_portable(text: str, *, vault: str, project: str, today: object) -> Optional[str]:
    """A vault rule page → its portable page, or ``None`` for a non-rule.

    ``None`` when *text* has no frontmatter, no ``slug``/``name``, or no
    ``type`` (the tree is laid out by type).

    Kept: ``name``, ``slug``, ``description``, ``type``, ``stability``, the
    topic tags (managed markers such as ``auto-promoted`` and
    ``needs-review`` never travel), ``confidence``, the ``evidence`` block,
    and the body with the graph section and mnemo's advisory notes removed.
    Dropped: ``sources``, every extractor timestamp, ``enforce``,
    ``activates_on``, and the ``demoted_from`` / ``promoted_without_enforce``
    / ``runtime`` stamps — they name local paths, local tools and local
    bookkeeping.

    Added: one ``published:`` block — ``vault`` (the id *vault*), ``project``
    (the publisher's name for this repo), ``date`` and ``source_count``.

    **What ``date`` means.** It is the date the publishing vault last wrote
    the rule — the newest of the page's own extractor timestamps — not the
    day ``publish`` ran. Re-publishing an unchanged page on any later day
    therefore produces identical bytes, which is what keeps the repo tree
    free of spurious diffs. *today* is used only for a page that carries no
    timestamp at all (a hand-written one), and then it is its first-publish
    date; ``publish`` may pass the date already in the tree to keep such a
    page stable too.

    **The hop rule.** When the page is itself imported
    (:func:`is_imported_frontmatter`), the provenance in its ``imported:``
    block is carried through unchanged — vault, project, date and count —
    and ``confidence: verified-elsewhere`` goes back to ``verified``. The
    rule still names the vault that observed it, not the one re-publishing
    it. *vault* and *project* are ignored for such a page.
    """
    fm, body = _split_page(text)
    if body is None:
        return None
    slug = derive_rule_slug(fm, "")
    page_type = fm.get("type")
    if not slug or not isinstance(page_type, str) or not page_type.strip():
        return None

    name = fm.get("name") if isinstance(fm.get("name"), str) and fm.get("name") else slug
    description = fm.get("description") if isinstance(fm.get("description"), str) else ""
    stability = fm.get("stability") if isinstance(fm.get("stability"), str) and fm.get("stability") else "stable"
    confidence = "verified" if fm.get("confidence") in ("verified", VERIFIED_ELSEWHERE) else "inferred"
    quote, source = _evidence(fm)

    provenance = _provenance(fm.get(IMPORTED_KEY)) if is_imported_frontmatter(fm) else None
    if provenance is None:
        sources = fm.get("sources")
        provenance = {
            "vault": vault,
            "project": project,
            "date": _page_date(fm, today),
            "source_count": len(sources) if isinstance(sources, list) else 0,
        }

    return (
        "---\n"
        f"name: {_yaml_scalar(name)}\n"
        f"slug: {_yaml_scalar(slug)}\n"
        f"description: {_yaml_scalar(description)}\n"
        f"type: {page_type.strip()}\n"
        f"stability: {stability}\n"
        f"confidence: {confidence}\n"
        f"{_block_list('tags', topic_tags(fm))}"
        f"{_evidence_block(quote, source)}"
        f"{_render_nested_block(PUBLISHED_KEY, provenance)}"
        "---\n\n"
        f"{_clean_body(body)}"
    )


def from_portable(text: str) -> Optional[PortableRule]:
    """The inverse read: a portable page → :class:`PortableRule`.

    ``None`` when the text carries no ``published:`` block naming a vault —
    a stray ``.md`` in the tree is not a rule and callers report it rather
    than stage it — or when it has no slug or type.
    """
    fm, body = _split_page(text)
    if body is None:
        return None
    provenance = _provenance(fm.get(PUBLISHED_KEY))
    if provenance is None:
        return None
    slug = derive_rule_slug(fm, "")
    page_type = fm.get("type")
    if not slug or not isinstance(page_type, str) or not page_type.strip():
        return None
    quote, source = _evidence(fm)
    return PortableRule(
        slug=slug,
        type=page_type.strip(),
        name=fm.get("name") if isinstance(fm.get("name"), str) and fm.get("name") else slug,
        description=fm.get("description") if isinstance(fm.get("description"), str) else "",
        body=_clean_body(body),
        tags=tuple(topic_tags(fm)),
        confidence="verified" if fm.get("confidence") == "verified" else "inferred",
        stability=fm.get("stability") if isinstance(fm.get("stability"), str) and fm.get("stability") else "stable",
        quote=quote,
        evidence_source=source,
        vault=provenance["vault"],
        project=provenance["project"],
        published_at=provenance["date"],
        source_count=provenance["source_count"],
        hash=portable_hash(text),
    )


def to_vault_page(rule: PortableRule, *, project: str, today: object) -> str:
    """A :class:`PortableRule` → the page ``import`` stages in ``shared/_inbox/<type>/``.

    Same key order and the same scalar quoting as
    ``extract/inbox/rendering._render_page``, so every reader of ``shared/``
    parses it. Differences from a native page, each one deliberate:

    * ``origin: imported`` — the stamp :func:`is_imported_frontmatter` reads.
    * ``confidence: verified-elsewhere`` when published ``verified``, else
      ``inferred``: someone else asked for this rule, and no "you said"
      surface (they all compare ``== "verified"``) may present it as the
      local user's own words.
    * ``projects:`` with the one *local* canonical project name and
      ``sources: []`` — ``projects_for_rule`` falls back to ``projects:``
      when the sources are empty, so every scope reader attributes the rule
      to this repo under the name this clone uses.
    * ``needs-review`` as the managed tag: the page is a draft until a
      human moves it.
    * an ``imported:`` block — the original provenance (vault, project,
      date, source_count) plus ``at``, the import date *today* — which
      :func:`to_portable` carries through on a re-publish.
    * never ``enforce`` and never ``activates_on``: a rule that can block a
      tool call is not something another vault gets to install.
    * secrets redacted (#418): the tree is someone else's text, and a
      ``shared/`` page is what the judges send off the machine.
    """
    def clean(text: str) -> str:
        return redact_secrets(text)[0]

    tags = ["needs-review"] + [t for t in rule.tags if t and t != "needs-review"]
    confidence = VERIFIED_ELSEWHERE if rule.confidence == "verified" else "inferred"
    imported = {
        "vault": rule.vault,
        "project": rule.project,
        "date": rule.published_at,
        "source_count": rule.source_count,
        "at": _iso_date(today),
    }
    return (
        "---\n"
        f"name: {_yaml_scalar(clean(rule.name))}\n"
        f"slug: {_yaml_scalar(rule.slug)}\n"
        f"description: {_yaml_scalar(clean(rule.description))}\n"
        f"type: {rule.type}\n"
        f"stability: {rule.stability}\n"
        f"confidence: {confidence}\n"
        f"origin: {IMPORTED}\n"
        f"{_block_list('projects', [project])}"
        "sources: []\n"
        f"{_block_list('tags', tags)}"
        f"{_evidence_block(clean(rule.quote) if rule.quote else rule.quote, rule.evidence_source)}"
        f"{_render_nested_block(IMPORTED_KEY, imported)}"
        "---\n\n"
        f"{clean(rule.body)}"
    )


def iter_portable(share_root: Union[str, Path]) -> List[Tuple[Path, Optional[PortableRule]]]:
    """Every ``<share_root>/<type>/*.md``, sorted by path.

    The second element is ``None`` for a file :func:`from_portable` refuses
    (or one that cannot be read), so the caller can report ``N files
    skipped, not mnemo pages`` by path. A missing *share_root* is an empty
    tree.
    """
    root = Path(share_root)
    if not root.is_dir():
        return []
    out: List[Tuple[Path, Optional[PortableRule]]] = []
    for md in sorted(root.glob("*/*.md")):
        if not md.is_file():
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            out.append((md, None))
            continue
        out.append((md, from_portable(text)))
    return out
