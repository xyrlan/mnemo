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

from mnemo.core.extract.inbox.rendering import _yaml_scalar
from mnemo.core.extract.source_paths import vault_relative_source
from mnemo.core.filters import MANAGED_TAGS, parse_frontmatter, topic_tags

_FM_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


class NotInsertOnly(ValueError):
    """:func:`merge_insert_only` was handed a pair that drops live content.

    Raised instead of silently behaving like :func:`replace_wholesale`, which is
    what the unguarded version did.
    """


class NoLiveFrontmatter(ValueError):
    """The live page has no ``---`` frontmatter block, so there is nothing to merge into.

    ``dedup_rules._merge_group_inplace`` takes the same posture ("canonical has
    no frontmatter; skip rather than corrupt"). Without it, ``_build`` starts
    from an empty frontmatter string, appends the proposal's scalars, and wraps
    the result in fresh fences — fabricating a rule out of a plain-text file.
    """


#: Keys the proposal is authoritative for.
_PROPOSAL_WINS = (
    "name",
    "description",
    "promoted_at",
    "extraction_run",
    "extracted_at",
    "last_sync",
)

#: Keys the live page is authoritative for. ``activates_on`` drives rule
#: activation and ``demoted_from`` records a reclassify decision; neither is the
#: extractor's to revise from a session transcript.
#:
#: ``stability`` sits here for a sharper reason. ``filters.is_consumer_visible``
#: treats ``stability: evolving`` as non-visible, and the extraction prompt
#: (``prompts/templates/few_shot_feedback.py``) shows the model emitting
#: ``evolving`` whenever a decision reads as still in flux. Proposal-wins would
#: therefore let one tentative-sounding transcript flip a stable, visible rule
#: to ``evolving`` and silently drop it from recall, reflex and export — which
#: is the exact failure #159 exists to fix, reintroduced by the fix for it.
_LIVE_WINS = ("confidence", "demoted_from", "activates_on", "enforce", "stability")

# The two policies must never overlap: a key in both would make the merge order
# decide the winner, silently. Convention is not enough when the cost of a wrong
# key is a corrupted rule.
assert not set(_PROPOSAL_WINS) & set(_LIVE_WINS), "a key cannot have two policies"


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

    It also inherits that helper's constraint, which is worth restating rather
    than gesturing at: the regex assumes a block list holds nothing but
    ``  - item`` lines. A hand-edited file with a comment or a blank line inside
    the block stops the match early, so the replacement splices in mid-block and
    the original tail survives as duplicated, misplaced content. Unreachable
    through this pipeline — neither ``rendering.py`` nor any writer here emits
    comments inside a list — so a human hand-edit is the only way in.
    """
    block_re = re.compile(
        rf"(?m)^{re.escape(key)}:[ \t]*(?:\[\])?[ \t]*\n(?:[ \t]+-[^\n]*\n?)*",
    )
    new_block = f"{key}: []" if not lines else f"{key}:\n" + "\n".join(f"  - {v}" for v in lines)
    if block_re.search(fm_text):
        return block_re.sub(lambda _m: new_block + "\n", fm_text, count=1).rstrip() + "\n"
    return fm_text.rstrip() + "\n" + new_block + "\n"


def _set_scalar(fm_text: str, key: str, value: Any) -> str:
    """Replace a scalar ``key: value`` line, or append it when absent.

    The value is re-rendered through the writer's own ``_yaml_scalar`` rather
    than interpolated raw. ``parse_frontmatter`` hands back *dequoted* values, so
    raw interpolation silently un-quotes whatever the writer had quoted: a
    description of ``#hashtag first`` became ``description: #hashtag first``,
    which this codebase's own reader tolerates but every standards-compliant
    YAML parser — Obsidian's included — reads as ``null`` plus a comment.
    ``_yaml_scalar`` also collapses embedded newlines, which would otherwise
    close the frontmatter block early or inject a bogus top-level key.
    """
    rendered = f"{key}: {_yaml_scalar(value)}"
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
    if not _FM_RE.match(live_text):
        # Skip rather than corrupt, exactly as dedup_rules._merge_group_inplace
        # does. Starting from an empty frontmatter string would append the
        # proposal's scalars and wrap them in fresh fences, turning a plain-text
        # or malformed live file into a rule with a fabricated name, description
        # and sources that never existed on the live side. ``classify`` only
        # requires the live file to exist, not to have frontmatter, so this is
        # reachable through the sanctioned pipeline.
        raise NoLiveFrontmatter("live page has no frontmatter block")

    live_fm_text, _live_body = _split(live_text)
    _prop_fm_text, prop_body = _split(proposal_text)
    live_fm = parse_frontmatter(live_text)
    prop_fm = parse_frontmatter(proposal_text)

    fm_text = live_fm_text
    for key in _PROPOSAL_WINS:
        if key in prop_fm and not isinstance(prop_fm[key], list):
            fm_text = _set_scalar(fm_text, key, prop_fm[key])
    # _LIVE_WINS needs no action: fm_text starts as the live frontmatter.
    #
    # Both blocks are rewritten only when at least one side actually had the key.
    # An earlier draft called _rewrite_block unconditionally, and since that
    # helper appends ``key: []`` for an absent key with no values, merging two
    # pages that both lacked ``tags:`` invented one. Confirmed on real files:
    # shared/project/clubinho__sprints-github.md and its staged proposal both
    # have no ``tags:`` block, and the merge grew a ``tags: []`` line. Harmless
    # to every reader (``parse_frontmatter`` treats ``[]`` and absent alike) but
    # this pipeline exists because silent frontmatter churn made 35 proposals
    # unreadable; adding keys a file never had is the same class of thing.
    if "sources" in live_fm or "sources" in prop_fm:
        fm_text = _rewrite_block(
            fm_text, "sources", _merged_sources(live_fm, prop_fm, vault_root)
        )
    if "tags" in live_fm or "tags" in prop_fm:
        fm_text = _rewrite_block(fm_text, "tags", _merged_tags(live_fm, prop_fm))

    return "---\n" + fm_text.rstrip() + "\n---\n" + prop_body


def merge_insert_only(live_text: str, proposal_text: str, *, vault_root: Path) -> str:
    """Merge an ``insert_only`` rewrite.

    The proposal body must be a superset of the live body, so taking it preserves
    every live line in order. That precondition is now *checked* rather than
    merely documented: handed a ``mixed`` pair, this used to behave exactly like
    :func:`replace_wholesale` and drop the live-only lines with no signal. The
    sanctioned path (``apply._ACTION_FOR_KIND``) never does that, but this is a
    plain public function and a repair script or REPL call would.

    The check reuses ``classify._classify_bodies`` so "insert only" has one
    definition in the codebase rather than two that can drift apart.
    """
    from mnemo.core.rewrites.classify import _classify_bodies, _split_body

    kind, _keep, _ins, _dropped = _classify_bodies(
        _split_body(live_text), _split_body(proposal_text)
    )
    if kind != "insert_only":
        raise NotInsertOnly(
            f"merge_insert_only requires an insert-only pair, got {kind!r}; "
            "use replace_wholesale if discarding live prose is intended"
        )
    return _build(live_text, proposal_text, vault_root=vault_root)


def replace_wholesale(live_text: str, proposal_text: str, *, vault_root: Path) -> str:
    """Take the proposal body for a ``mixed`` or ``full_rewrite`` acceptance.

    Body handling is identical to :func:`merge_insert_only`; the separate name is
    the point. Here the caller is knowingly discarding live prose, which is why
    ``apply`` archives the pristine original before writing.
    """
    return _build(live_text, proposal_text, vault_root=vault_root)
