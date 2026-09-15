"""The "Existing rules" hint shown in the consolidation user message.

Each extraction used to mint a fresh slug for a rule the vault already held,
so ``source_count`` never accrued and near-duplicate families grew. Listing the
live and staged slugs for the chunk's projects lets the model reinforce an
existing rule instead; ``inbox/dedup._detect_similar_existing`` (Task 7) is
the mechanical backstop when it does not.

Advertising the slug alone turned out to cause a second failure (#184): the
model was told to reuse a slug whose text it had never read, so it restated the
rule from the fresh transcript and dropped the specifics the live page carried
(a six-step recipe, an env-var name, a gotcha). Re-summarizing is the correct
answer to that prompt — the prompt was asking the wrong question.

So the fragment now shows the *body* of the few rules the current chunk looks
likely to touch, with an explicit edit contract. Bodies are the expensive part
(median 216 tokens; all ``MAX_ENTRIES`` of them would be ~17k per chunk), which
is why relevance gates them: everything else stays a one-line ``slug — name``.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract.scanner import _normalize_slug
from mnemo.core.filters import parse_frontmatter
from mnemo.core.rule_activation import is_universal, projects_for_rule

MAX_ENTRIES = 80
_UNIVERSAL_THRESHOLD = 2

#: At most this many rule bodies are quoted in one fragment. The whole point is
#: to stay cheap: 3 × ~216 median tokens is ~650, against ~17k for all 80.
MAX_BODIES = 3

#: Characters of one rule body to quote. Above the p90 page (~323 tokens) so a
#: typical rule is shown whole — a half-shown recipe is worse than none, since
#: the model completes it from imagination.
MAX_BODY_CHARS = 1600

#: Minimum weighted-Jaccard similarity between a chunk's text and a live rule
#: before that rule's body is worth its tokens. Deliberately well under
#: ``dedup.SIMILARITY_THRESHOLD`` (0.32, the bar for asserting two pages are the
#: *same* rule): here a false positive costs a few hundred tokens, while a false
#: negative costs the specifics the rule was written for. See
#: ``dedup._weighted_profile`` for why real-page scores top out near 0.5.
BODY_RELEVANCE_THRESHOLD = 0.12

# One extraction run reads the same vault directories once per chunk per kind,
# which is ~1s of parsing per pass at 1.4k pages. The vault only changes when
# apply_pages writes, so the run clears this between kinds rather than paying
# the scan again for every chunk.
#: slug -> (name, projects, source_count, description, body)
_CACHE: dict[tuple[str, str], list[tuple[str, str, list[str], int, str, str]]] = {}


def clear_cache() -> None:
    """Drop the per-run scan cache. Call after pages are written to the vault."""
    _CACHE.clear()


def _slug_for(frontmatter: dict, stem: str) -> str:
    """The identifier the model must echo to reinforce this page.

    Deliberately NOT ``filters.derive_rule_slug``: that helper falls back to
    the human-readable ``name`` before the stem, which is right for matching a
    legacy page but wrong here — we are handing the model a slug to emit, and a
    rule page is always written to disk under its slug. A display name like
    "Use yarn" would be echoed back verbatim and mint a new page.

    Normalized through the same ``_normalize_slug`` the response parser applies,
    so an advertised slug round-trips: a page at ``Ask_Before_Refactor.md``
    must be shown as ``ask-before-refactor``, which is what the echoed slug
    becomes on re-entry — advertising the raw stem would mint a duplicate.
    """
    slug = frontmatter.get("slug")
    if isinstance(slug, str) and slug.strip():
        return _normalize_slug(slug.strip())
    return _normalize_slug(stem)


def _body_of(text: str) -> str:
    """The page body with frontmatter and the generated graph section removed.

    The ``## Sources`` block is machine-written wikilinks; quoting it back at
    the model adds tokens and invites it to invent source paths.
    """
    body = text
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4:]
    marker = body.find("<!-- mnemo:graph-section -->")
    if marker != -1:
        body = body[:marker]
    return body.strip()


def _collect(vault_root: Path, kind: str) -> list[tuple[str, str, list[str], int, str, str]]:
    key = (str(vault_root), kind)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    out = []
    for d in (vault_root / "shared" / kind, vault_root / "shared" / "_inbox" / kind):
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            if md.name.endswith(".proposed.md") or md.name.endswith(".update-proposed.md"):
                continue
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
                fm = parse_frontmatter(text)
            except (OSError, ValueError):
                continue
            sources = fm.get("sources") or []
            if isinstance(sources, str):
                sources = [sources]
            sources = [s for s in sources if isinstance(s, str)]
            out.append((_slug_for(fm, md.stem), str(fm.get("name") or md.stem),
                        projects_for_rule(sources, frontmatter=fm), len(sources),
                        str(fm.get("description") or ""), _body_of(text)))
    _CACHE[key] = out
    return out


def _truncate_body(body: str) -> str:
    """Cut an over-long body at a line boundary and say so.

    The note matters: without it the model reads a truncated recipe as the
    whole rule and "completes" the missing steps in its rewrite.
    """
    if len(body) <= MAX_BODY_CHARS:
        return body
    cut = body[:MAX_BODY_CHARS]
    nl = cut.rfind("\n")
    if nl > MAX_BODY_CHARS // 2:
        cut = cut[:nl]
    return cut.rstrip() + "\n[... rule continues — this is an excerpt, not the whole rule]"


def _relevant_bodies(
    rows: list[tuple[int, str, str, str, str]],
    chunk: list,
) -> list[tuple[str, str]]:
    # rows are (source_count, slug, name, description, body)
    """Pick the (slug, body) pairs whose rule the chunk plausibly restates.

    Scored with the same weighted-Jaccard the dedup backstop uses, so the two
    halves of the reuse story agree on what "the same rule" looks like; see
    [[similarity-calibration-2026-09-02]] for why the thresholds are low.
    """
    from mnemo.core.extract.inbox.dedup import _weighted_profile, weighted_jaccard

    probe_text = "\n".join(
        f"{getattr(f, 'slug', '')}\n{getattr(f, 'body', '')}" for f in chunk
    )
    if not probe_text.strip():
        return []
    probe = _weighted_profile("", "", probe_text)
    scored: list[tuple[float, str, str]] = []
    for _count, slug, name, description, body in rows:
        if not body:
            continue
        score = weighted_jaccard(probe, _weighted_profile(name, description, body))
        if score >= BODY_RELEVANCE_THRESHOLD:
            scored.append((score, slug, body))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [(slug, body) for _score, slug, body in scored[:MAX_BODIES]]


def existing_rules_fragment(
    vault_root: Path | None,
    kind: str,
    *,
    agents: set[str],
    chunk: list | None = None,
) -> str:
    """Render the slug list for *kind*, scoped to *agents* (plus universal rules).

    When *chunk* is given (the ``MemoryFile`` list being consolidated), the
    bodies of up to :data:`MAX_BODIES` rules the chunk looks likely to restate
    are quoted in full, with the edit contract that stops #184's re-summarize.
    Callers that omit it keep the cheap one-line-per-rule shape.
    """
    if vault_root is None:
        return ""
    rows = []
    for slug, name, projects, count, description, body in _collect(vault_root, kind):
        # An empty `projects` means no bots/ source could be attributed, so the
        # rule belongs to no project in particular — listed for every chunk.
        if agents and projects and not (set(projects) & agents) \
                and not is_universal(projects, _UNIVERSAL_THRESHOLD):
            continue
        rows.append((count, slug, name, description, body))
    if not rows:
        return ""
    rows.sort(key=lambda r: (-r[0], r[1]))
    listed = rows[:MAX_ENTRIES]
    # Relevance scores every eligible rule, not just the listed slice. Sorting
    # by source_count and cutting at MAX_ENTRIES is a popularity filter, and the
    # rule a chunk is about to overwrite is usually an unpopular one: measured
    # on the real vault, all four of #184's re-summarized rules scored 0.32-0.53
    # against their own proposal and every one of them fell outside the top 80.
    bodies = _relevant_bodies(rows, chunk) if chunk else []
    # A rule worth quoting is worth naming, even if source_count buried it.
    listed_slugs = {slug for _c, slug, _n, _d, _b in listed}
    quoted_slugs = {slug for slug, _b in bodies} - listed_slugs
    extra = [
        (slug, name)
        for _c, slug, name, _d, _b in rows
        if slug in quoted_slugs
    ]
    lines = [f"- {slug} — {name}" for _, slug, name, _d, _b in listed]
    lines += [f"- {slug} — {name}" for slug, name in extra]
    out = (
        f"Existing rules for {kind} (REUSE the slug when your page states the same "
        f"rule — only mint a new slug for a genuinely new rule):\n"
        + "\n".join(lines)
        + "\n\n"
    )
    if bodies:
        blocks = "\n\n".join(
            f"### {slug}\n{_truncate_body(body)}" for slug, body in bodies
        )
        out += (
            "You may be about to edit these existing rules. This is their "
            "CURRENT text — read it before reusing the slug:\n\n"
            f"{blocks}\n\n"
            "When you emit one of these slugs you are proposing a REPLACEMENT "
            "for the text above. So: keep every specific it already carries "
            "(commands, file paths, env-var names, numbers, gotchas) verbatim, "
            "and only add what the input files genuinely establish, or correct "
            "what they show to be wrong. Never restate the rule in your own "
            "words, never generalise a concrete step, and never drop a detail "
            "because the input files did not mention it. If you have nothing "
            "to add or correct, do not emit that slug at all.\n\n"
        )
    return out
