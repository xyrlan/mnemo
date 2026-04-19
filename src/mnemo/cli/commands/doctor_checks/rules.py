"""Rule-integrity + universal-promotion doctor checks.

Hosts :func:`_doctor_check_rule_integrity` (frontmatter validation +
source-path resolution + body length floor) and
:func:`_doctor_check_universal_promotion` (informational health line
about how many rules sit one project away from auto-promotion).
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
    ``shared/_inbox/``, ``needs-review``-tagged, ``stability: evolving``) are
    excluded to avoid noise on transient extraction artefacts.
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
                continue  # draft / needs-review / evolving — skip integrity

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


def _doctor_check_universal_promotion(vault: Path) -> bool:
    """Report universal-promotion health: count + on-verge rules.

    Returns True always — this is an informational check, not a pass/fail gate.
    (If there is no index yet, we still print a placeholder line for consistency.)
    """
    from mnemo.core import rule_activation
    from mnemo.core.config import load_config

    idx = rule_activation.load_index(vault)
    if idx is None or "rules" not in idx:
        print("Universal promotion health: index unavailable (run a SessionStart).")
        return True

    threshold = int(load_config().get("scoping", {}).get("universalThreshold", 2))
    universal_slugs = idx.get("universal", {}).get("slugs", [])
    universal_topics = idx.get("universal", {}).get("topics", [])

    on_verge: list[str] = [
        slug for slug, rule in idx["rules"].items()
        if not rule.get("universal")
        and len(rule.get("projects", [])) == threshold - 1
    ]

    print(f"Universal promotion health: {len(universal_slugs)} universal rule(s).")
    if universal_topics:
        print("  Top universal topics: " + ", ".join(universal_topics[:5]))
    if on_verge:
        print(
            f"  {len(on_verge)} rule(s) one project away from promotion: "
            + ", ".join(sorted(on_verge)[:5])
        )
    return True
