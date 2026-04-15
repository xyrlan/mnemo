"""Shared filter predicate + minimal frontmatter reader (v0.4).

This module is the single source of truth for deciding which pages in
``shared/`` are "consumer-visible". Both the v0.4 HOME dashboard and the v0.5
MCP tools MUST call :func:`is_consumer_visible` so human-view and machine-view
stay in lockstep.

See ``project_mnemo_v0.4_direction.md`` → "Shared filter specification".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

MANAGED_TAGS: frozenset[str] = frozenset(
    {"needs-review", "auto-promoted", "home", "dashboard", "wiki", "index"}
)


def is_consumer_visible(
    page_path: Path,
    frontmatter: dict[str, Any],
    vault_root: Path,
) -> bool:
    """Return True if the page should appear in consumer surfaces.

    Three conditions, short-circuited in order:
    1. Path filter — anything under ``shared/_inbox/`` is draft.
    2. ``needs-review`` tag — explicit user-reviewable marker.
    3. ``stability: evolving`` — decision still in flux.
    """
    try:
        rel = page_path.relative_to(vault_root / "shared")
    except ValueError:
        return False
    if rel.parts and rel.parts[0] == "_inbox":
        return False
    tags = frontmatter.get("tags") or []
    if "needs-review" in tags:
        return False
    if (frontmatter.get("stability") or "stable") == "evolving":
        return False
    return True


def topic_tags(frontmatter: dict[str, Any]) -> list[str]:
    """Return just the user-facing topic tags, stripping managed markers."""
    return [t for t in (frontmatter.get("tags") or []) if t not in MANAGED_TAGS]


def collect_existing_tags(vault_root: Path, page_type: str) -> list[str]:
    """Scan ``shared/<page_type>/*.md`` and return the sorted union of topic tags.

    Used by the extraction prompt builders to inject a controlled-vocabulary
    hint into the LLM prompt. Only reads the target type's directory so each
    page type gets its own clean vocab (v0.4 decision).

    Ignores ``shared/_inbox/`` drafts (the filter treats them as non-canonical).
    Reserved/system markers never appear in the result because ``topic_tags``
    strips them.
    """
    type_dir = vault_root / "shared" / page_type
    if not type_dir.is_dir():
        return []
    collected: set[str] = set()
    for md in type_dir.glob("*.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        for t in topic_tags(fm):
            collected.add(t)
    return sorted(collected)


def _parse_inline_list(value: str) -> list[str]:
    # parse [a, b, c] → ["a", "b", "c"]
    inner = value[1:-1]
    return [item.strip() for item in inner.split(",") if item.strip()]


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse the specific YAML-ish shape that mnemo writes.

    Supports:
    - scalar strings (``key: value``)
    - inline empty lists (``key: []``)
    - block-style string lists (``key:\\n  - item``)
    - one level of nested dicts (``key:\\n  subkey: value``) where subkey
      values can be scalars, inline lists, or 4-space-indented block lists

    Anything else is ignored. This is not a general YAML parser — we control
    the writer, so the reader can be strict. Lines without a colon (outside a
    block list) are skipped silently to survive hand edits.
    """
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    body = text[4:end]

    out: dict[str, Any] = {}
    current_list_key: str | None = None   # top-level block-list key
    current_dict_key: str | None = None   # nested-dict parent key
    current_sublist_key: str | None = None  # subkey collecting 4-space block list

    for raw in body.splitlines():
        if not raw.strip():
            current_list_key = None
            current_dict_key = None
            current_sublist_key = None
            continue

        # 4-space block-list item inside a nested dict subkey
        if raw.startswith("    - ") and current_dict_key is not None and current_sublist_key is not None:
            out[current_dict_key][current_sublist_key].append(raw[6:].strip())
            continue

        # 2-space block-list item — could be top-level list or dict continuation
        if raw.startswith("  - "):
            if current_list_key is not None:
                # top-level block list (already established)
                out[current_list_key].append(raw[4:].strip())
                continue
            if current_dict_key is not None and isinstance(out.get(current_dict_key), dict):
                # first  "  - " after a bare key: this is a top-level block list
                # convert the dict placeholder back to a list
                out[current_dict_key] = [raw[4:].strip()]
                current_list_key = current_dict_key
                current_dict_key = None
                current_sublist_key = None
                continue

        # 0-indent block-list item (existing parser handled it)
        if raw.startswith("- ") and current_list_key is not None:
            out[current_list_key].append(raw[2:].strip())
            continue

        # 2-space indented subkey line inside a nested dict
        if raw.startswith("  ") and not raw.startswith("   ") and current_dict_key is not None:
            subline = raw[2:]
            if ":" not in subline:
                continue
            subkey, _, subval = subline.partition(":")
            subkey = subkey.strip()
            subval = subval.strip()
            if not subkey:
                continue
            current_sublist_key = None  # reset unless we open a sub-block list
            if subval == "[]":
                out[current_dict_key][subkey] = []
            elif subval.startswith("[") and subval.endswith("]"):
                out[current_dict_key][subkey] = _parse_inline_list(subval)
            elif subval == "":
                # subkey starts a 4-space block list
                out[current_dict_key][subkey] = []
                current_sublist_key = subkey
            else:
                out[current_dict_key][subkey] = subval
            continue

        # top-level key line (no leading spaces)
        current_list_key = None
        current_dict_key = None
        current_sublist_key = None
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value == "":
            # ambiguous until next line: could be block list or nested dict
            # initialise as dict; if "  - " comes next we'll convert to list
            out[key] = {}
            current_dict_key = key
        elif value == "[]":
            out[key] = []
        else:
            out[key] = value
    return out
