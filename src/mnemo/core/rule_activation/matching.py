"""Runtime match helpers: bash deny matching, path-glob enrichment.

Houses the ``EnforceHit`` / ``EnrichHit`` dataclasses that the PreToolUse
hook consumes, plus the bash-command normaliser and the two per-project
iterators. Imports ``_glob_matches`` from :mod:`globs`.

Extracted from the v0.8 rule_activation.py monolith in v0.9 PR G.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from mnemo.core.rule_activation.globs import _glob_matches, _glob_to_regex
from mnemo.core.rule_activation.parsing import _MATCH_FLAGS


# ---------------------------------------------------------------------------
# Dataclasses — public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnforceHit:
    slug: str
    project: str
    reason: str
    path: str = ""   # filled by match_bash_enforce when available


@dataclass(frozen=True)
class EnrichHit:
    slug: str
    project: str
    rule_body_preview: str


# ---------------------------------------------------------------------------
# Bash normalisation
# ---------------------------------------------------------------------------


def normalize_bash_command(cmd: str) -> str:
    """Normalise a Bash command for deny_command prefix matching.

    Repeatedly strips leading ``sudo`` / ``env`` / shell-inline ``KEY=VAL``
    prefixes until idempotent, then lowercases. Looping is required because
    real-world bypass attempts chain these (e.g. ``sudo sudo ...``,
    ``env A=1 env B=2 ...``, ``sudo -E env FOO=1 ...``).

    Sudo flags that take an argument (``-u root``, ``-g wheel``, etc.) are
    handled explicitly — otherwise a naive flag strip leaves the flag's
    value sitting at the start of the command and makes ``sudo -u root git
    push --force`` look like ``root git push --force``.
    """
    # Collapse whitespace first so the regex anchors work cleanly.
    cmd = re.sub(r"\s+", " ", cmd).strip()

    prev: str | None = None
    while prev != cmd:
        prev = cmd
        # sudo, optionally followed by any mix of:
        #   - flags that take an argument:  -u root, -g wheel, -p prompt, ...
        #   - plain flags:                  -E, -s, --preserve-env, ...
        # Matches ``sudo`` on its own as well (the (?:...)* allows zero flags).
        cmd = re.sub(
            r"^sudo\s+(?:(?:-[uUgpCcDhrTR]\s+\S+|-\S+)\s+)*",
            "",
            cmd,
            flags=re.IGNORECASE,
        )
        # env followed by one-or-more KEY=VAL tokens.
        cmd = re.sub(r"^env\s+(?:\w+=\S*\s+)+", "", cmd, flags=re.IGNORECASE)
        # Bare shell-inline KEY=VAL assignments (FOO=bar BAZ=qux git ...).
        cmd = re.sub(r"^(?:\w+=\S*\s+)+", "", cmd)

    return re.sub(r"\s+", " ", cmd).strip().lower()


# ---------------------------------------------------------------------------
# Match logic
# ---------------------------------------------------------------------------


def match_bash_enforce(index: dict, project: str, command: str) -> EnforceHit | None:
    """Test command against the project's enforce rules.

    Hard cap: truncates to 4 KB BEFORE any matching.
    Reads from v2 layout: iterates by_project[proj].local_slugs + universal.slugs,
    reads the enforce block from rules[slug].
    """
    if len(command) > 4096:
        command = command[:4096]

    normalized = normalize_bash_command(command)

    rules_table = index.get("rules", {})
    local_slugs = index.get("by_project", {}).get(project, {}).get("local_slugs", [])
    universal_slugs = index.get("universal", {}).get("slugs", [])
    # De-dupe while preserving priority: local first, then universal
    seen: set[str] = set()
    ordered_slugs: list[str] = []
    for slug in list(local_slugs) + list(universal_slugs):
        if slug in seen:
            continue
        seen.add(slug)
        ordered_slugs.append(slug)

    for slug in ordered_slugs:
        rule = rules_table.get(slug)
        if not rule:
            continue
        enforce = rule.get("enforce")
        if not enforce:
            continue

        for pattern in enforce.get("deny_patterns", []):
            try:
                if re.search(pattern, command, _MATCH_FLAGS):
                    return EnforceHit(
                        slug=slug,
                        project=project,
                        reason=enforce.get("reason", slug),
                        path=rule.get("path", ""),
                    )
            except re.error:
                continue

        for dc_normalized in enforce.get("deny_commands", []):
            if normalized == dc_normalized or normalized.startswith(dc_normalized + " "):
                return EnforceHit(
                    slug=slug,
                    project=project,
                    reason=enforce.get("reason", slug),
                    path=rule.get("path", ""),
                )

    return None


def names_a_file(glob: str) -> bool:
    """True when *glob* names one file: a literal path, optionally under ``**/``.

    ``prisma/schema.prisma``, ``app.json`` and ``**/screens/HomeScreen.tsx``
    name a file; ``src/app/dashboard/**``, ``**/*.ts`` and ``**/actions.ts``
    name an area. Only the first kind
    injects on a file in focus (#271): replayed over 150 real sessions'
    Read/Edit/Write calls, file globs injected a mean of 1.6 notes per session
    (max 13, none at the 15-per-session cap); letting literal directory globs
    in as well raised it to 6.2, with 23 sessions at the cap. An area is what
    the prompt-time reflex is for.
    """
    if glob.startswith("**/"):
        # ``**/actions.ts`` is every actions.ts in the tree; a directory above
        # the name is what pins it to one file.
        literal = glob[3:]
        return "/" in literal and not re.search(r"[*?\[]", literal)
    return bool(glob) and not re.search(r"[*?\[]", glob)


def match_path_enrich(
    index: dict,
    project: str,
    file_path: str,
    tool_name: str,
) -> list[EnrichHit]:
    """Return up to 3 matching EnrichHit for *file_path* filtered by *tool_name*.

    *file_path* is repo-relative (the hook makes it so). Only globs that name
    a file take part (:func:`names_a_file`). ``Read`` matches every
    rule — opening the file is when its notes are wanted, before the edit is
    planned; the rule's ``tools`` list governs the writing tools only.

    Ordered by source_count desc, then slug asc. Reads v2 layout.
    """
    rules_table = index.get("rules", {})
    local_slugs = index.get("by_project", {}).get(project, {}).get("local_slugs", [])
    universal_slugs = index.get("universal", {}).get("slugs", [])

    seen: set[str] = set()
    candidates: list[dict] = []
    for slug in list(local_slugs) + list(universal_slugs):
        if slug in seen:
            continue
        seen.add(slug)
        rule = rules_table.get(slug)
        if not rule:
            continue
        enrich = rule.get("activates_on")
        if not enrich:
            continue
        if tool_name != "Read" and tool_name not in enrich.get("tools", []):
            continue
        for glob in enrich.get("path_globs", []):
            if names_a_file(glob) and _glob_matches(glob, file_path):
                candidates.append({
                    "slug": slug,
                    "source_count": enrich.get("source_count", 0),
                    "rule_body_preview": enrich.get("rule_body_preview", ""),
                })
                break

    candidates.sort(key=lambda r: (-r["source_count"], r["slug"]))

    return [
        EnrichHit(slug=c["slug"], project=project, rule_body_preview=c["rule_body_preview"])
        for c in candidates[:3]
    ]


def iter_enforce_rules_for_project(index: dict, project: str):
    """Yield rule dicts that carry an enforce block and are visible from *project*.

    Visibility = local to project OR universal. De-duplicated on slug.
    Each yielded dict is the per-slug rule entry from ``index["rules"]``
    with an additional ``slug`` key injected for convenience.
    """
    rules_table = index.get("rules", {})
    local_slugs = index.get("by_project", {}).get(project, {}).get("local_slugs", [])
    universal_slugs = index.get("universal", {}).get("slugs", [])
    seen: set[str] = set()
    for slug in list(local_slugs) + list(universal_slugs):
        if slug in seen:
            continue
        seen.add(slug)
        rule = rules_table.get(slug)
        if rule and rule.get("enforce"):
            yield {"slug": slug, **rule}


def iter_enrich_rules_for_project(index: dict, project: str):
    """Yield rule dicts with an activates_on block visible from *project*.

    Each yielded dict is the per-slug rule entry from ``index["rules"]``
    with an additional ``slug`` key injected for convenience.
    """
    rules_table = index.get("rules", {})
    local_slugs = index.get("by_project", {}).get(project, {}).get("local_slugs", [])
    universal_slugs = index.get("universal", {}).get("slugs", [])
    seen: set[str] = set()
    for slug in list(local_slugs) + list(universal_slugs):
        if slug in seen:
            continue
        seen.add(slug)
        rule = rules_table.get(slug)
        if rule and rule.get("activates_on"):
            yield {"slug": slug, **rule}


__all__ = [
    "EnforceHit",
    "EnrichHit",
    "_glob_matches",
    "_glob_to_regex",
    "iter_enforce_rules_for_project",
    "iter_enrich_rules_for_project",
    "match_bash_enforce",
    "match_path_enrich",
    "names_a_file",
    "normalize_bash_command",
]
