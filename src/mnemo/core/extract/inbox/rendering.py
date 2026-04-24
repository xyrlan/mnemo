"""YAML scalar / list / nested-block + page rendering helpers.

Pulled out of the pre-v0.9 ``inbox.py`` monolith into its own module
so the decision-table apply logic and the rendering vocabulary evolve
independently. Consumed by ``apply.py`` and the ``branches/*`` modules
plus a few tests that exercise rendering directly.
"""
from __future__ import annotations

from mnemo.core.extract.inbox.types import ExtractedPage

_YAML_SPECIALS = (":", "#", '"', "'", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "%", "@", "`")


def _yaml_scalar(value: object) -> str:
    """Serialize *value* as a minimal YAML-safe scalar.

    Quotes only when the string contains YAML-special characters or leading/
    trailing whitespace. Uses single-quoted form with doubled inner quotes —
    the cheapest form that round-trips cleanly through the parse_frontmatter
    extension from Task 1 (which strips a matched pair of surrounding
    single/double quotes via ``_dequote``).
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value)
    if s == "":
        return "''"
    needs_quoting = (
        s != s.strip()
        or any(c in s for c in _YAML_SPECIALS)
    )
    if not needs_quoting:
        return s
    escaped = s.replace("'", "''")
    return f"'{escaped}'"


def _yaml_inline_list(items: list) -> str:
    return "[" + ", ".join(_yaml_scalar(i) for i in items) + "]"


def _render_nested_block(key: str, data: dict) -> str:
    """Render a top-level key whose value is a dict of scalars / lists.

    Writes::

        key:
          subkey: scalar
          subkey2: [a, b, c]
          subkey3:
            - long item one
            - long item two

    Conforms to the Task 1 parse_frontmatter extension — 2-space indent for
    subkeys, 4-space indent for sub-block-list items.
    """
    lines = [f"{key}:"]
    for subkey, subval in data.items():
        if isinstance(subval, list):
            # Inline short, block long. path_globs always goes block.
            if subkey == "path_globs":
                use_inline = False
            else:
                total_len = sum(len(str(i)) for i in subval) + 2 * len(subval)
                use_inline = len(subval) <= 4 and total_len < 60
            if use_inline:
                lines.append(f"  {subkey}: {_yaml_inline_list(subval)}")
            else:
                lines.append(f"  {subkey}:")
                for item in subval:
                    lines.append(f"    - {_yaml_scalar(item)}")
        else:
            lines.append(f"  {subkey}: {_yaml_scalar(subval)}")
    return "\n".join(lines) + "\n"


def _render_page(page: ExtractedPage, *, run_id: str, auto_promoted: bool = False) -> str:
    sources_yaml = "\n".join(f"  - {s}" for s in page.source_files)

    # --- enforce block ---
    # Safety rail (C3, 2026-04-23): auto-promoted pages are LLM-authored
    # and unreviewed. Stripping the enforce block prevents a single briefing
    # line from becoming a session-wide hard-block. Manual promotion paths
    # (auto_promoted=False) still honor the enforce block.
    enforce_block = ""
    enforce_stripped = False
    if isinstance(page.enforce, dict) and page.enforce:
        if auto_promoted:
            enforce_stripped = True
        else:
            enforce_block = _render_nested_block("enforce", page.enforce)

    if auto_promoted:
        extras = f"last_sync: {run_id}\n"
        if enforce_stripped:
            extras += "promoted_without_enforce: true\n"
        system_marker = "auto-promoted"
    else:
        extras = ""
        system_marker = "needs-review"

    stability = getattr(page, "stability", None) or "stable"
    # Unified tags list: system marker first, then LLM-emitted topic tags.
    # The shared filter (core/filters.py) reads this same list; topic_tags()
    # strips the marker when bucketing by topic in the HOME dashboard.
    page_tags = list(getattr(page, "tags", None) or [])
    all_tags = [system_marker]
    for t in page_tags:
        if t and t != system_marker and t not in all_tags:
            all_tags.append(t)
    tags_yaml = "\n".join(f"  - {t}" for t in all_tags)
    # Optional activation blocks — only written when non-empty. Both flow
    # through _render_nested_block so indentation is guaranteed to match
    # what parse_frontmatter (Task 1 extension) expects.
    activates_on_block = ""
    if isinstance(page.activates_on, dict) and page.activates_on:
        activates_on_block = _render_nested_block("activates_on", page.activates_on)

    body_prefix = ""
    if enforce_stripped:
        body_prefix = (
            "> _mnemo auto-promoter stripped an `enforce:` block from this rule._\n"
            "> _Review the pattern and re-add manually if safe. "
            "See docs/superpowers/plans/2026-04-23-enforce-safety-rails.md._\n\n"
        )

    return (
        "---\n"
        f"name: {_yaml_scalar(page.name)}\n"
        f"description: {_yaml_scalar(page.description)}\n"
        f"type: {page.type}\n"
        f"extracted_at: {run_id}\n"
        f"extraction_run: {run_id}\n"
        f"stability: {stability}\n"
        f"{extras}"
        "sources:\n"
        f"{sources_yaml}\n"
        "tags:\n"
        f"{tags_yaml}\n"
        f"{enforce_block}"
        f"{activates_on_block}"
        "---\n\n"
        f"{body_prefix}{page.body}\n"
    )


def _extract_body(text: str) -> str:
    """Return the markdown body of a mnemo-written page (strip frontmatter)."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + len("\n---\n"):]
