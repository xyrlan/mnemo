"""Rule-integrity + universal-promotion doctor checks.

Hosts :func:`_doctor_check_rule_integrity` (frontmatter validation +
source-path resolution + body length floor) and
:func:`_doctor_check_universal_promotion` (informational health line
reporting universal rule count + cross-project promotion candidates).
"""
from __future__ import annotations

from pathlib import Path

_MIN_BODY_CHARS = 50


def _doctor_check_rule_integrity(vault: Path) -> bool:
    """Validate canonical rules in shared/{feedback,user,reference}/.

    Checks, per file:
      - frontmatter parses non-empty
      - required fields present: type, tags (non-empty), sources (non-empty)
      - every source path resolves under the vault
      - body (text after frontmatter) >= _MIN_BODY_CHARS
    Files that the shared filter marks as non-canonical (drafts in
    ``shared/_inbox/``, ``stability: evolving``) are excluded to avoid noise
    on transient extraction artefacts.
    """
    from mnemo.core.filters import is_consumer_visible, parse_frontmatter
    from mnemo.core.mcp.tools import _RETRIEVAL_TYPES, _extract_body

    shared = vault / "shared"
    if not shared.is_dir():
        return True

    ok = True
    for page_type in _RETRIEVAL_TYPES:
        type_dir = shared / page_type
        if not type_dir.is_dir():
            continue
        for md_path in sorted(type_dir.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                fm = parse_frontmatter(text)
            except Exception:
                fm = {}

            if not fm:
                print(f"  \u26a0 Rule {page_type}/{md_path.name}: frontmatter unparseable or missing")
                print(f"       \u2192 re-run 'mnemo extract' or fix the frontmatter manually")
                ok = False
                continue

            if not is_consumer_visible(md_path, fm, vault):
                continue  # draft / evolving — skip integrity

            rel = f"{page_type}/{md_path.name}"
            if not fm.get("type"):
                print(f"  \u26a0 Rule {rel}: missing 'type' field in frontmatter")
                ok = False
            if not fm.get("tags"):
                print(f"  \u26a0 Rule {rel}: 'tags' field is empty or missing")
                ok = False

            sources = fm.get("sources") or []
            if not sources:
                print(f"  \u26a0 Rule {rel}: 'sources' field is empty or missing")
                ok = False
            else:
                for src in sources:
                    if not isinstance(src, str):
                        continue
                    if not (vault / src).is_file():
                        print(f"  \u26a0 Rule {rel}: source path does not resolve: {src}")
                        ok = False

            body = _extract_body(text).strip()
            if len(body) < _MIN_BODY_CHARS:
                print(f"  \u26a0 Rule {rel}: body has {len(body)} chars (min {_MIN_BODY_CHARS})")
                ok = False

    return ok


def _doctor_check_missing_slugs(vault: Path) -> bool:
    """#114: pages without ``slug:`` are keyed by display name until migrated.

    Dry-run only — doctor reports, the next session start or ``mnemo extract``
    stamps. Always advisory.
    """
    from mnemo.core.migrations import slugs as _slugs

    rep = _slugs.stamp_slugs(vault, dry_run=True)
    if rep.stamped == 0:
        print("  \u2713 every rule page carries slug:")
        return True
    print(
        f"  \u26a0 {rep.stamped} page(s) missing slug: \u2014 the next session start "
        "or `mnemo extract` migrates them"
    )
    return True


def _doctor_check_bare_deny_command(vault: Path) -> bool:
    """Warn when the activation index contains rules rejected for bare deny_command.

    These rules authored a ``deny_command`` without a ``deny_pattern`` qualifier
    and were silently dropped from the index when the C2 safety rail landed in
    v0.11. The author needs to either add a ``deny_pattern`` regex or remove the
    enforce block entirely.

    Returns True always — this is an advisory check; the index-build step already
    records the rejection.
    """
    from mnemo.core import rule_activation

    idx = rule_activation.load_index(vault)
    if idx is None:
        return True

    malformed = idx.get("malformed") or []
    bare_deny = [m for m in malformed if "deny_command requires a qualifier" in m.get("error", "")]
    if bare_deny:
        print(f"  ⚠ {len(bare_deny)} rule(s) have bare `deny_command` without `deny_pattern` — "
              f"add a regex qualifier or remove the enforce block. Paths:")
        for m in bare_deny[:5]:
            print(f"    • {m['path']}")
        if len(bare_deny) > 5:
            print(f"    … {len(bare_deny) - 5} more")
    return True


def _doctor_check_stripped_enforce(vault: Path, idx: object = None) -> bool:
    """Warn when auto-promoted rules had their enforce block stripped.

    These files carry ``promoted_without_enforce: true`` in their frontmatter.
    A human must review the rule and re-add the enforce block manually if the
    pattern is safe. Always returns True — advisory check, not a pass/fail gate.
    """
    from mnemo.core.filters import iter_shared_pages

    stripped_paths = []
    for md in iter_shared_pages(vault, include_inbox=False):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
            if "promoted_without_enforce: true" in text.split("---", 2)[1]:
                stripped_paths.append(md)
        except (IndexError, OSError):
            continue

    if stripped_paths:
        print(
            f"  ⚠ {len(stripped_paths)} auto-promoted rule(s) had enforce block stripped — "
            f"review and re-add manually if the pattern is safe:"
        )
        for p in stripped_paths[:5]:
            print(f"    • {p.relative_to(vault)}")
        if len(stripped_paths) > 5:
            print(f"    … {len(stripped_paths) - 5} more")
    return True


def _doctor_check_universal_promotion(vault: Path) -> bool:
    """Report universal-promotion health: count + cross-project candidates.

    Returns True always — this is an informational check, not a pass/fail gate.
    (If there is no index yet, we still print a placeholder line for consistency.)
    """
    from mnemo.core import rule_activation
    from mnemo.core.universal_candidates import find_universal_candidates

    idx = rule_activation.load_index(vault)
    if idx is None or "rules" not in idx:
        print("Universal promotion health: index unavailable (run a SessionStart).")
        return True

    universal_slugs = idx.get("universal", {}).get("slugs", [])
    universal_topics = idx.get("universal", {}).get("topics", [])

    print(f"Universal promotion health: {len(universal_slugs)} universal rule(s).")
    if universal_topics:
        print("  Top universal topics: " + ", ".join(universal_topics[:5]))

    # Counting rules at `threshold - 1` projects used to be reported as "one
    # project away", but with the default threshold of 2 that is every
    # single-project rule in the vault (541 of 556 on the v0.15.1 dogfood) —
    # noise, not a signal. Exact-slug promotion effectively never fires, so
    # what is actionable is the differently-named pair teaching one lesson.
    candidates = find_universal_candidates(idx)
    if candidates:
        print(
            f"  {len(candidates)} cross-project promotion candidate(s) "
            f"(near-duplicate rules in different projects):"
        )
        for c in candidates[:5]:
            print(f"    • [{'+'.join(c.projects)}] sim={c.similarity:.2f}")
            for slug in c.slugs:
                print(f"        - {slug}")
        if len(candidates) > 5:
            print(f"    … {len(candidates) - 5} more")
        print("    (merge with `mnemo dedup-rules` after aligning names, or ignore)")
    return True


def _is_backfill_staged(vault: Path, key: str) -> bool:
    """True when the _inbox file behind a state key carries the backfill stamp.

    Origin lives on disk, not in the state file: the state schema has no
    ``origin`` field, so reading it back is a one-file read keyed off
    ``<type>/<slug>`` that needs no migration for vaults written before the
    backfill feature landed.

    Parses with ``filters.parse_frontmatter`` — the nesting-aware reader —
    because that is the parser written for the shape mnemo itself writes, and
    it does not false-positive on an unrelated nested ``origin`` subkey.
    Whether the stamp is nested or top-level is *not* load-bearing here:
    :func:`is_backfill_frontmatter` accepts both, so this check cannot drift
    away from the routing gate the way it did before Task 6c.
    """
    from mnemo.core.backfill.origin import is_backfill_frontmatter
    from mnemo.core.filters import parse_frontmatter

    page_type, _, slug = key.partition("/")
    if not slug:
        return False
    md = vault / "shared" / "_inbox" / page_type / f"{slug}.md"
    try:
        fm = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # missing file, unreadable bytes, malformed frontmatter
        return False
    return is_backfill_frontmatter(fm)


def _doctor_check_unpromoted_universal_candidates(vault: Path) -> bool:
    """Warn when extraction-state has _inbox entries that cross the
    universal threshold but never moved into ``shared/<type>/``.

    Diagnoses the v0.15 dogfood gap: ``_union_with_prior_sources`` accrued
    cross-project ``source_files`` but the apply dispatcher had no branch
    that would promote them. The reconciler in :mod:`mnemo.core.extract`
    clears that backlog on every extract, so a live entry sitting above the
    threshold means the user is reading doctor between extracts.

    Backfill-origin entries are exempt: the origin gate keeps them staged
    permanently and by design, so they are not a backlog and no amount of
    ``mnemo extract`` will move them. They are reported separately as a
    neutral status line.

    Always returns True — advisory check, not a gate.
    """
    from mnemo.core.config import load_config
    from mnemo.core.extract.inbox.state_io import load_state
    from mnemo.core.rule_activation.index import is_universal, projects_for_rule

    state_path = vault / ".mnemo" / "extraction-state.json"
    if not state_path.exists():
        return True
    try:
        state = load_state(state_path)
    except Exception:
        return True

    threshold = int(load_config().get("scoping", {}).get("universalThreshold", 2))
    pending: list[str] = []
    backfill_staged: list[str] = []
    for key, entry in state.entries.items():
        if entry.status != "inbox":
            continue
        if _is_backfill_staged(vault, key):
            # Awaiting human review, not awaiting reconciliation. Counted
            # regardless of threshold — project-type staged pages are
            # single-source and never cross it.
            backfill_staged.append(key)
            continue
        projects = projects_for_rule(list(entry.source_files))
        if is_universal(projects, threshold):
            pending.append(key)

    if pending:
        print(
            f"  ⚠ {len(pending)} rule(s) cross universalThreshold but remain in _inbox/ — "
            f"run 'mnemo extract' to reconcile:"
        )
        for k in sorted(pending)[:5]:
            print(f"    • {k}")
        if len(pending) > 5:
            print(f"    … {len(pending) - 5} more")

    if backfill_staged:
        print(
            f"  {len(backfill_staged)} backfill rule(s) staged in _inbox/ awaiting review "
            "(reconstructed from old transcripts, so they stage by design):"
        )
        for k in sorted(backfill_staged)[:5]:
            print(f"    • shared/_inbox/{k}.md")
        if len(backfill_staged) > 5:
            print(f"    … {len(backfill_staged) - 5} more")
        print(
            "    (read each one; move keepers to shared/<same type>/, "
            "e.g. shared/_inbox/project/ → shared/project/, and delete the rest)"
        )
        print(
            "    (a plain mv is enough — leave the needs-review tag; the move "
            "is what makes it live, from your next session on)"
        )
    return True
