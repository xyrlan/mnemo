"""Reflex BM25F index: build, write, load.

Vault-wide — mirrors rule_activation-index.json structure. Project filtering
happens at query time via per-doc ``projects`` + ``universal`` fields (see
retrieval.py / user_prompt_submit hook). Same ``is_consumer_visible`` gate as
``rule_activation.build_index`` — non-negotiable parity with the HOME
dashboard.

Schema v1:
    {
      "schema_version": 1,
      "generated_at": "YYYY-MM-DDTHH:MM:SSZ",
      "avg_field_length": {name, topic_tags, aliases, description, body},
      "doc_count": int,
      "postings": { term: [{"slug": ..., "tf": {field: count}}, ...] },
      "docs": {
        slug: {
          "field_length": {field: int},
          "preview": str,
          "stability": "stable" | "evolving",
          "projects": list[str],
          "universal": bool,
        },
      },
    }
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mnemo.core.filters import is_consumer_visible, parse_frontmatter
from mnemo.core.reflex.tokenizer import tokenize
from mnemo.core.rule_activation import _is_universal, projects_for_rule
from mnemo.core.text_utils import body_preview

SCHEMA_VERSION = 1
INDEX_FILENAME = "reflex-index.json"

_FIELD_NAMES = ("name", "topic_tags", "aliases", "description", "body")
_SYSTEM_TAGS: frozenset[str] = frozenset({"auto-promoted", "needs-review"})


def _field_tokens(fm: dict, body_text: str, slug: str) -> dict[str, list[str]]:
    """Extract token lists per indexed field."""
    name = fm.get("name") or slug
    tags = fm.get("tags") or []
    topic_tags = [t for t in tags if isinstance(t, str) and t not in _SYSTEM_TAGS]
    aliases_raw = fm.get("aliases") or []
    aliases = [a for a in aliases_raw if isinstance(a, str)]
    description = fm.get("description") or ""

    return {
        "name": tokenize(str(name)),
        "topic_tags": [t for tag in topic_tags for t in tokenize(tag)],
        "aliases": [t for alias in aliases for t in tokenize(alias)],
        "description": tokenize(str(description)),
        "body": tokenize(body_text),
    }


def build_index(vault_root: Path, *, universal_threshold: int = 2) -> dict:
    """Walk shared/{feedback,user,reference}/*.md, build the BM25F index."""
    docs: dict[str, dict] = {}
    postings: dict[str, list[dict]] = {}
    field_length_totals = {f: 0 for f in _FIELD_NAMES}

    for page_type in ("feedback", "user", "reference"):
        type_dir = vault_root / "shared" / page_type
        if not type_dir.is_dir():
            continue

        for md_path in sorted(type_dir.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8")
            except OSError:
                continue

            fm = parse_frontmatter(text)
            if not is_consumer_visible(md_path, fm, vault_root):
                continue

            slug = fm.get("slug") or fm.get("name") or md_path.stem

            sources_raw = fm.get("sources") or []
            if isinstance(sources_raw, str):
                sources_raw = [sources_raw]
            source_files = [s for s in sources_raw if isinstance(s, str)]
            projects = projects_for_rule(source_files)
            universal = _is_universal(projects, universal_threshold)

            field_toks = _field_tokens(fm, text, slug)
            field_length = {f: len(field_toks[f]) for f in _FIELD_NAMES}

            # Merge all field tokens into postings with per-field tf.
            for field, toks in field_toks.items():
                seen: dict[str, int] = {}
                for tok in toks:
                    seen[tok] = seen.get(tok, 0) + 1
                for tok, tf in seen.items():
                    postings.setdefault(tok, [])
                    # Find or create entry for this slug.
                    bucket = None
                    for entry in postings[tok]:
                        if entry["slug"] == slug:
                            bucket = entry
                            break
                    if bucket is None:
                        bucket = {"slug": slug, "tf": {f: 0 for f in _FIELD_NAMES}}
                        postings[tok].append(bucket)
                    bucket["tf"][field] = tf

            for f in _FIELD_NAMES:
                field_length_totals[f] += field_length[f]

            docs[slug] = {
                "field_length": field_length,
                "preview": body_preview(text, max_chars=300),
                "stability": fm.get("stability") or "stable",
                "projects": projects,
                "universal": universal,
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


def write_index(vault_root: Path, index: dict) -> None:
    """Atomic write. Never raises during test runs; callers should still try/except."""
    path = vault_root / ".mnemo" / INDEX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(json.dumps(index, indent=2).encode("utf-8"))
    os.replace(tmp, path)


def load_index(vault_root: Path) -> dict | None:
    """Load the index from disk. Returns None on ANY error. Never raises."""
    try:
        path = vault_root / ".mnemo" / INDEX_FILENAME
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        if raw.get("schema_version") != SCHEMA_VERSION:
            return None
        return raw
    except Exception:  # noqa: BLE001 — fail-open
        return None
