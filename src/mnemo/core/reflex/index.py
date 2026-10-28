"""Reflex BM25F index: build, write, load.

Vault-wide — mirrors rule_activation-index.json structure. Project filtering
happens at query time via per-doc ``projects`` + ``universal`` fields (see
retrieval.py / user_prompt_submit hook). Same ``is_consumer_visible`` gate as
``rule_activation.build_index`` — non-negotiable parity with the HOME
dashboard.

In memory — what :func:`build_index` returns and what every reader sees:
    {
      "schema_version": 2,
      "generated_at": "YYYY-MM-DDTHH:MM:SSZ",
      "avg_field_length": {name, topic_tags, aliases, description, body, evidence},
      "doc_count": int,
      "postings": { term: [{"slug": ..., "tf": {field: count}}, ...] },
      "docs": {
        slug: {
          "field_length": {field: int},
          "preview": str,
          "path": str,
          "stability": "stable" | "evolving",
          "projects": list[str],
          "universal": bool,
          "retired": bool,
        },
      },
    }

On disk (schema v2, #779) the same object is written compactly: ``doc_ids``
lists the slugs, and each term's postings are one string of space-separated
entries, a doc id followed by a letter and a count per field the term occurs
in (``"17b3 204n1b2"``: doc 17 has it 3 times in its body, doc 204 once in its
name and twice in its body). v1 wrote every entry as a dict carrying the slug
and all six counts, zeros included, with ``indent=2``: 137 MB and a 0.6 s
parse on a 4,078-rule vault, paid by every prompt that reached ranking. The
same index is 7 MB in v2 and parses in ~0.05 s (``tools/measure_reflex_index_load.py``).
:func:`load_index` decodes a term's string only when it is looked up, so a
prompt pays for its own few terms, not for 50K. A v1 file still loads, as it
always did, until the next SessionStart rebuild rewrites it as v2.

The evidence quote is the user's own words from a feedback correction — it is
the best lexical bridge to how they phrase the same complaint again, so it is
indexed and scored as its own field.

``path`` is the page's path under the vault root, POSIX-style: the hook reads
the body of the rules it emits from it (#542), a handful of files per prompt
instead of every body in an index loaded on every prompt. An index written
before the key existed has none; the hook then emits the preview.

``retired`` is :func:`filters.is_retired` against the ledger, read here once
per page the way ``stability`` is. A retired rule stays indexed — its postings
and length still count toward the corpus statistics, so retiring one rule does
not re-score every other — and ``decide.candidates_for_project`` drops it.
That filter is the one place injection, ``replay`` and ``mnemo why`` learn a
rule was retired. An index written before the key existed reads as not
retired.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from mnemo.core.errors import load_validated_json
from mnemo.core.filters import derive_rule_slug, is_consumer_visible, is_retired
from mnemo.core.reclassify_types import split_frontmatter
from mnemo.core.reflex.tokenizer import tokenize
from mnemo.core.rule_activation import is_universal, projects_for_rule
from mnemo.core.text_utils import body_preview, retrieval_body

SCHEMA_VERSION = 2
# The versions :func:`load_index` reads: v1 has the in-memory shape on disk.
_READABLE_VERSIONS = (1, 2)
INDEX_FILENAME = "reflex-index.json"

_FIELD_NAMES = ("name", "topic_tags", "aliases", "description", "body", "evidence")
_SYSTEM_TAGS: frozenset[str] = frozenset({"auto-promoted", "needs-review"})
# One letter per field in a v2 posting string; ``_FIELD_NAMES`` order.
_FIELD_CODES = "ntadbe"
_CODE_FIELD = dict(zip(_FIELD_CODES, _FIELD_NAMES))
_ENTRY_RE = re.compile(r"(\d+)((?:[a-z]\d+)*)")
_COUNT_RE = re.compile(r"([a-z])(\d+)")


def _field_tokens(fm: dict, body_text: str, slug: str) -> dict[str, list[str]]:
    """Extract token lists per indexed field."""
    name = fm.get("name") or slug
    tags = fm.get("tags") or []
    topic_tags = [t for t in tags if isinstance(t, str) and t not in _SYSTEM_TAGS]
    aliases_raw = fm.get("aliases") or []
    aliases = [a for a in aliases_raw if isinstance(a, str)]
    description = fm.get("description") or ""
    evidence = fm.get("evidence")
    quote = str(evidence.get("quote") or "") if isinstance(evidence, dict) else ""

    return {
        "name": tokenize(str(name)),
        "topic_tags": [t for tag in topic_tags for t in tokenize(tag)],
        "aliases": [t for alias in aliases for t in tokenize(alias)],
        "description": tokenize(str(description)),
        "body": tokenize(body_text),
        "evidence": tokenize(quote),
    }


def build_index(vault_root: Path, *, universal_threshold: int = 2) -> dict:
    """Walk shared/{feedback,user,reference,project}/*.md, build the BM25F index."""
    docs: dict[str, dict] = {}
    postings: dict[str, list[dict]] = {}
    field_length_totals = {f: 0 for f in _FIELD_NAMES}

    for page_type in ("feedback", "user", "reference", "project"):
        type_dir = vault_root / "shared" / page_type
        if not type_dir.is_dir():
            continue

        for md_path in sorted(type_dir.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            # Frontmatter is metadata (keys, source paths, run ids); only
            # the body is rule text (#137).
            fm, body = split_frontmatter(text)
            if not is_consumer_visible(md_path, fm, vault_root):
                continue

            slug = derive_rule_slug(fm, md_path.stem)

            sources_raw = fm.get("sources") or []
            if isinstance(sources_raw, str):
                sources_raw = [sources_raw]
            source_files = [s for s in sources_raw if isinstance(s, str)]
            projects = projects_for_rule(source_files, frontmatter=fm)
            universal = is_universal(projects, universal_threshold)

            # Tokenize what retrieval sees, not the page (text_utils.retrieval_body).
            indexed_text = retrieval_body(body)
            field_toks = _field_tokens(fm, indexed_text, slug)
            field_length = {f: len(field_toks[f]) for f in _FIELD_NAMES}

            # Merge all field tokens into postings with per-field tf. This
            # page's bucket per term is kept here rather than found by
            # scanning postings[tok], which made the build quadratic in the
            # pages sharing a term (#610: 24 s of a 3,400-rule SessionStart).
            buckets: dict[str, dict] = {}
            for field, toks in field_toks.items():
                seen: dict[str, int] = {}
                for tok in toks:
                    seen[tok] = seen.get(tok, 0) + 1
                for tok, tf in seen.items():
                    bucket = buckets.get(tok)
                    if bucket is None:
                        bucket = {"slug": slug, "tf": {f: 0 for f in _FIELD_NAMES}}
                        buckets[tok] = bucket
                        postings.setdefault(tok, []).append(bucket)
                    bucket["tf"][field] = tf

            for f in _FIELD_NAMES:
                field_length_totals[f] += field_length[f]

            docs[slug] = {
                "field_length": field_length,
                "preview": body_preview(text, max_chars=300),
                "path": md_path.relative_to(vault_root).as_posix(),
                "stability": fm.get("stability") or "stable",
                "projects": projects,
                "universal": universal,
                "retired": is_retired(fm, vault_root=vault_root),
            }

    doc_count = len(docs)
    avg_field_length = {
        f: (field_length_totals[f] / doc_count) if doc_count else 0.0
        for f in _FIELD_NAMES
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "avg_field_length": avg_field_length,
        "doc_count": doc_count,
        "postings": postings,
        "docs": docs,
    }


def _encode_postings(entries: list, doc_ids: dict[str, int]) -> str:
    parts = []
    for entry in entries:
        tf = entry["tf"]
        parts.append(str(doc_ids[entry["slug"]]) + "".join(
            code + str(int(tf[field]))
            for code, field in zip(_FIELD_CODES, _FIELD_NAMES) if tf.get(field)
        ))
    return " ".join(parts)


def _decode_postings(encoded: str, slugs: list[str]) -> list[dict]:
    out = []
    for doc_id, counts in _ENTRY_RE.findall(encoded):
        tf = {f: 0 for f in _FIELD_NAMES}
        for code, n in _COUNT_RE.findall(counts):
            tf[_CODE_FIELD[code]] = int(n)
        out.append({"slug": slugs[int(doc_id)], "tf": tf})
    return out


def encode_index(index: dict) -> bytes:
    """The schema v2 bytes for an in-memory index (see the module docstring)."""
    docs = index.get("docs") or {}
    slugs = list(docs)
    doc_ids = {slug: i for i, slug in enumerate(slugs)}
    on_disk = {k: v for k, v in index.items() if k != "postings"}
    on_disk["schema_version"] = SCHEMA_VERSION
    on_disk["doc_ids"] = slugs
    on_disk["postings"] = {
        term: _encode_postings(entries, doc_ids)
        for term, entries in (index.get("postings") or {}).items()
    }
    return json.dumps(on_disk, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class LazyPostings(Mapping):
    """A v2 file's postings, decoded per term on first lookup.

    Reads like the in-memory ``{term: [{"slug", "tf"}, ...]}``. A lookup of
    one term decodes that term; iterating ``items()`` or ``values()`` decodes
    them all, which a batch reader such as ``replay`` may do once.
    """

    def __init__(self, encoded: dict[str, str], slugs: list[str]) -> None:
        self._encoded = encoded
        self._slugs = slugs
        self._decoded: dict[str, list[dict]] = {}

    def __getitem__(self, term: str) -> list[dict]:
        entries = self._decoded.get(term)
        if entries is None:
            entries = _decode_postings(self._encoded[term], self._slugs)
            self._decoded[term] = entries
        return entries

    def __iter__(self) -> Iterator[str]:
        return iter(self._encoded)

    def __len__(self) -> int:
        return len(self._encoded)

    def __contains__(self, term: object) -> bool:
        return term in self._encoded


def write_index(vault_root: Path, index: dict) -> None:
    """Atomic write. Never raises during test runs; callers should still try/except.

    Goes through the shared mkstemp writer: concurrent SessionStarts rebuild
    this index at once, and the old fixed ``.tmp`` sibling let one writer's
    ``os.replace`` consume the other's tmp file (FileNotFoundError, four
    occurrences in the 2026-08 window).
    """
    from mnemo.core.atomic import atomic_write_bytes

    path = vault_root / ".mnemo" / INDEX_FILENAME
    atomic_write_bytes(path, encode_index(index))


def load_index(vault_root: Path) -> dict | None:
    """Load the index from disk. Returns None on any error. Never raises.

    A v2 file comes back in the in-memory shape, its postings a
    :class:`LazyPostings`; a v1 file comes back as it was written.
    """
    data = load_validated_json(
        vault_root / ".mnemo" / INDEX_FILENAME,
        _READABLE_VERSIONS,
        vault_root=vault_root,
        error_namespace="reflex.index.load",
    )
    if data is None or data.get("schema_version") == 1:
        return data
    slugs = data.pop("doc_ids", None)
    postings = data.get("postings")
    if not isinstance(slugs, list) or not isinstance(postings, dict):
        return None
    data["postings"] = LazyPostings(postings, slugs)
    return data
