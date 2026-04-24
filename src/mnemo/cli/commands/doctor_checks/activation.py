"""Activation-related doctor checks.

Hosts :func:`_doctor_check_activation` (malformed/stale/over-broad
activation blocks) and :func:`_doctor_check_activation_fidelity`
(positive round-trip self-check that every enforce/activates_on rule
reaches the index and self-activates on a synthesized path).
"""
from __future__ import annotations

from pathlib import Path

from mnemo.cli._helpers import _synthesize_path_for_glob


def _doctor_check_activation_fidelity(vault: Path) -> bool:
    """Positive self-check: every enforce/activates_on rule reaches the index
    and its globs actually match a synthesized path.

    Complements `_doctor_check_activation` (which validates malformed blocks)
    with a round-trip: if the frontmatter parses but the slug never enters the
    index or never self-activates on a representative path, something broke
    between authoring and dispatch.

    Returns True iff no warnings were emitted. `ℹ` info lines (for rules whose
    globs are un-synthesizable) are NOT warnings.
    """
    from mnemo.core.filters import derive_rule_slug, parse_frontmatter
    from mnemo.core.rule_activation import (
        load_index,
        match_path_enrich,
        parse_activates_on_block,
        parse_enforce_block,
    )

    index = load_index(vault)
    if index is None:
        return True  # no index yet; _doctor_check_activation surfaces staleness

    indexed_slugs: set[str] = {
        slug for slug, rule in index.get("rules", {}).items()
        if rule.get("enforce") or rule.get("activates_on")
    }

    ok = True

    feedback_dir = vault / "shared" / "feedback"
    if feedback_dir.is_dir():
        for md_path in sorted(feedback_dir.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                fm = parse_frontmatter(text)
            except Exception:
                continue

            slug = derive_rule_slug(fm, md_path.stem)
            rel = md_path.name

            _parsed_enforce, _enforce_err = parse_enforce_block(fm)
            has_enforce = _parsed_enforce is not None
            has_enrich = parse_activates_on_block(fm) is not None

            if has_enforce and slug not in indexed_slugs:
                print(f"  \u26a0 Rule {rel!r} has a valid enforce block but is absent from the activation index")
                print(f"       \u2192 run 'mnemo extract' to rebuild the index")
                ok = False
            if has_enrich and slug not in indexed_slugs:
                print(f"  \u26a0 Rule {rel!r} has a valid activates_on block but is absent from the activation index")
                print(f"       \u2192 run 'mnemo extract' to rebuild the index")
                ok = False

    for slug, rule_entry in index.get("rules", {}).items():
        activates = rule_entry.get("activates_on")
        if not activates:
            continue
        # Use first associated project for self-activation test; universal rules
        # are reachable from any project so we just pick one from by_project.
        rule_projects = rule_entry.get("projects", [])
        project = rule_projects[0] if rule_projects else ""
        globs = activates.get("path_globs", []) or []
        tools = activates.get("tools", []) or []
        if not globs or not tools:
            continue
        any_testable = False
        mismatched = False
        for glob in globs:
            sample = _synthesize_path_for_glob(glob)
            if sample is None:
                continue
            any_testable = True
            for tool in tools:
                hits = match_path_enrich(index, project, sample, tool)
                if slug not in [h.slug for h in hits]:
                    print(f"  \u26a0 Rule {slug!r} does not self-activate: glob {glob!r} -> synthesized {sample!r}, tool {tool!r} returned no hit")
                    print(f"       \u2192 review the glob shape or the enrich build pipeline")
                    ok = False
                    mismatched = True
                    break
            if mismatched:
                break
        if not any_testable and not mismatched:
            print(f"  \u2139 Rule {slug!r} has no auto-testable path_globs (contains '?' or '[abc]' \u2014 manual verification required)")

    return ok


def _doctor_check_activation(vault: Path) -> bool:
    """Four activation-related doctor checks:

    1. Malformed activate/enforce blocks in feedback files.
    2. Stale activation index (index mtime older than newest feedback file mtime).
    3. Suspicious deny_pattern (< 5 chars, or matches "echo hello").
    4. Overly-broad activates_on.path_globs (**/* or *).

    Each check is fail-safe: a bad file is skipped, not a crash.
    Returns True if no warnings were emitted.
    """
    import re as _re
    from mnemo.core.filters import parse_frontmatter
    from mnemo.core.rule_activation import parse_block

    feedback_dir = vault / "shared" / "feedback"
    ok = True

    if not feedback_dir.is_dir():
        return True

    candidates = sorted(feedback_dir.glob("*.md"))
    newest_mtime: float = 0.0
    for md_path in candidates:
        try:
            mtime = md_path.stat().st_mtime
            if mtime > newest_mtime:
                newest_mtime = mtime
        except OSError:
            pass

    # --- Check 1: malformed blocks ---
    # --- Check 3: suspicious deny_pattern ---
    # --- Check 4: overly-broad path_globs ---
    _BENIGN_TEST_INPUT = "echo hello"
    _BROAD_GLOBS = {"**/*", "*"}

    for md_path in candidates:
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # skip unreadable files

        try:
            fm = parse_frontmatter(text)
        except Exception:
            continue

        rel = md_path.name

        # --- Check 1: enforce block present-but-invalid ---
        if fm.get("enforce") is not None:
            parsed, err = parse_block("enforce", fm)
            if parsed is None:
                print(f"  \u26a0 Malformed enforce block in {rel}: {err}")
                print(f"       \u2192 fix the frontmatter in shared/feedback/{rel}")
                ok = False
            else:
                # --- Check 3: suspicious deny_pattern ---
                for pattern in parsed.get("deny_patterns", []):
                    suspicious = False
                    reason = ""
                    if len(pattern) < 5:
                        suspicious = True
                        reason = f"pattern {pattern!r} is shorter than 5 characters"
                    elif _re.search(pattern, _BENIGN_TEST_INPUT, _re.IGNORECASE | _re.DOTALL):
                        suspicious = True
                        reason = f"pattern {pattern!r} matches benign input {_BENIGN_TEST_INPUT!r} — too permissive"
                    if suspicious:
                        print(f"  \u26a0 Suspicious deny_pattern in {rel}: {reason}")
                        print(f"       \u2192 tighten the pattern so it doesn't match safe commands")
                        ok = False

        # --- Check 1: activates_on block present-but-invalid ---
        if fm.get("activates_on") is not None:
            parsed_enrich, err = parse_block("activates_on", fm)
            if parsed_enrich is None:
                print(f"  \u26a0 Malformed activates_on block in {rel}: {err}")
                print(f"       \u2192 fix the frontmatter in shared/feedback/{rel}")
                ok = False
            else:
                # --- Check 4: overly-broad path_globs ---
                for glob in parsed_enrich.get("path_globs", []):
                    if glob in _BROAD_GLOBS:
                        print(f"  \u26a0 Overly-broad path_glob {glob!r} in {rel}: matches virtually every file")
                        print(f"       \u2192 narrow the glob (e.g. **/*.py, src/**/*.ts) to avoid false positives")
                        ok = False

    # --- Check 2: stale activation index ---
    index_path = vault / ".mnemo" / "rule-activation-index.json"
    if index_path.exists() and newest_mtime > 0:
        try:
            index_mtime = index_path.stat().st_mtime
            if index_mtime < newest_mtime:
                import datetime as _dt
                def _fmt(ts: float) -> str:
                    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
                print(f"  \u26a0 Activation index is stale (newest feedback file: {_fmt(newest_mtime)}, index: {_fmt(index_mtime)}). Run 'mnemo extract' to rebuild.")
                ok = False
        except OSError:
            pass

    return ok
