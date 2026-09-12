"""Build the merged page text for an accepted staged rewrite.

Two named entry points rather than one with a flag, so the call site states which
semantic it intends — the thing that was previously implicit in a manual ``mv``.

Frontmatter is rebuilt key-by-key from a measured policy (see the plan's Task 4
table), not taken wholesale from either side. Taking the proposal's frontmatter
wholesale drops live ``sources[]`` entries and copies a ``needs-review`` marker
onto a rule a human already reviewed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mnemo.core.extract.source_paths import vault_relative_source
from mnemo.core.filters import MANAGED_TAGS, parse_frontmatter, topic_tags

_FM_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)

#: Keys the proposal is authoritative for.
_PROPOSAL_WINS = (
    "name",
    "description",
    "promoted_at",
    "extraction_run",
    "extracted_at",
    "last_sync",
    "stability",
)

#: Keys the live page is authoritative for. ``activates_on`` drives rule
#: activation and ``demoted_from`` records a reclassify decision; neither is the
#: extractor's to revise from a session transcript.
_LIVE_WINS = ("confidence", "demoted_from", "activates_on", "enforce")


def _split(text: str) -> tuple[str, str]:
    """``(frontmatter_text, body)``. Frontmatter text excludes the ``---`` fences."""
    m = _FM_RE.match(text)
    if not m:
        return "", text
    return m.group(1), m.group(2)


def _rewrite_block(fm_text: str, key: str, lines: list[str]) -> str:
    """Replace a ``key:`` block with rendered list lines, or append if absent.

    Same surgical approach as ``dedup_rules._rewrite_block``: only the named
    block is touched so every other key keeps its quoting byte-for-byte.
    """
    block_re = re.compile(
        rf"(?m)^{re.escape(key)}:[ \t]*(?:\[\])?[ \t]*\n(?:[ \t]+-[^\n]*\n?)*",
    )
    new_block = f"{key}: []" if not lines else f"{key}:\n" + "\n".join(f"  - {v}" for v in lines)
    if block_re.search(fm_text):
        return block_re.sub(lambda _m: new_block + "\n", fm_text, count=1).rstrip() + "\n"
    return fm_text.rstrip() + "\n" + new_block + "\n"


def _set_scalar(fm_text: str, key: str, value: Any) -> str:
    """Replace a scalar ``key: value`` line, or append it when absent."""
    rendered = f"{key}: {value}"
    line_re = re.compile(rf"(?m)^{re.escape(key)}:[ \t]*[^\n]*$")
    if line_re.search(fm_text):
        return line_re.sub(lambda _m: rendered, fm_text, count=1)
    return fm_text.rstrip() + "\n" + rendered + "\n"


def _merged_sources(live_fm: dict, prop_fm: dict, vault_root: Path) -> list[str]:
    out: list[str] = []
    for fm in (live_fm, prop_fm):
        raw = fm.get("sources") or []
        if isinstance(raw, str):
            raw = [raw]
        for s in raw:
            if not isinstance(s, str):
                continue
            norm = vault_relative_source(s, vault_root)
            if norm not in out:
                out.append(norm)
    return out


def _merged_tags(live_fm: dict, prop_fm: dict) -> list[str]:
    """Live managed marker + union of topic tags (live order first).

    The proposal's topic tags are usually better (`frontend-gotchas`: live
    ``workflow, testing`` → proposal ``react, testing, ui, css``), but its
    managed marker is not: a staged page always carries ``needs-review``, and
    copying that onto a live rule re-marks a reviewed page as a draft.
    """
    live_managed = [t for t in (live_fm.get("tags") or []) if t in MANAGED_TAGS]
    out = list(live_managed)
    for t in topic_tags(live_fm) + topic_tags(prop_fm):
        if t not in out:
            out.append(t)
    return out


def _build(live_text: str, proposal_text: str, *, vault_root: Path) -> str:
    live_fm_text, _live_body = _split(live_text)
    _prop_fm_text, prop_body = _split(proposal_text)
    live_fm = parse_frontmatter(live_text)
    prop_fm = parse_frontmatter(proposal_text)

    fm_text = live_fm_text
    for key in _PROPOSAL_WINS:
        if key in prop_fm and not isinstance(prop_fm[key], list):
            fm_text = _set_scalar(fm_text, key, prop_fm[key])
    # _LIVE_WINS needs no action: fm_text starts as the live frontmatter.
    fm_text = _rewrite_block(fm_text, "sources", _merged_sources(live_fm, prop_fm, vault_root))
    fm_text = _rewrite_block(fm_text, "tags", _merged_tags(live_fm, prop_fm))

    return "---\n" + fm_text.rstrip() + "\n---\n" + prop_body


def merge_insert_only(live_text: str, proposal_text: str, *, vault_root: Path) -> str:
    """Merge an ``insert_only`` rewrite.

    The proposal body is a superset of the live body (every non-equal opcode is
    an insert), so taking it preserves every live line in order — verified by
    ``classify``, and re-asserted in this module's tests.
    """
    return _build(live_text, proposal_text, vault_root=vault_root)


def replace_wholesale(live_text: str, proposal_text: str, *, vault_root: Path) -> str:
    """Take the proposal body for a ``mixed`` or ``full_rewrite`` acceptance.

    Body handling is identical to :func:`merge_insert_only`; the separate name is
    the point. Here the caller is knowingly discarding live prose, which is why
    ``apply`` archives the pristine original before writing.
    """
    return _build(live_text, proposal_text, vault_root=vault_root)
