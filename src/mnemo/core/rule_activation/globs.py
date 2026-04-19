"""Glob-to-regex translation with ``**`` semantics.

Leaf module — depends only on stdlib. Imported by ``matching.py`` (for
runtime path matching) and ``parsing.py`` (for parse-time validation of
``activates_on.path_globs``).

Both helpers were extracted verbatim from the v0.8 rule_activation.py
monolith in v0.9 PR G.
"""
from __future__ import annotations

import re


def _glob_matches(glob: str, path: str) -> bool:
    """Match *path* against *glob* with proper ``**`` semantics.

    Rules:
    - ``**`` (or ``**/``) matches any number of path segments including zero,
      crossing ``/`` boundaries.
    - A single ``*`` does NOT cross ``/`` boundaries.
    - ``**/<pattern>`` matches ``<pattern>`` in any subdirectory OR at the root.
    - ``[!abc]`` is a negated character class (glob syntax), translated to
      regex ``[^abc]``.

    Malformed globs (e.g. unterminated ``[``) return False — they are
    rejected at parse time via ``parse_activates_on_block``, so reaching
    this point with one indicates a stale or hand-crafted index.
    """
    # Normalise path separators
    path = path.replace("\\", "/")
    glob = glob.replace("\\", "/")

    regex = _glob_to_regex(glob)
    if regex is None:
        return False
    try:
        return bool(re.fullmatch(regex, path))
    except re.error:
        return False


def _glob_to_regex(glob: str) -> str | None:
    """Convert a glob pattern (with ``**`` support) to a regex string.

    Returns None if the glob is structurally invalid (e.g. unterminated
    bracket expression). Callers must treat None as "does not match
    anything" at runtime and as a parse-time rejection signal.
    """
    regex_parts: list[str] = []
    i = 0
    n = len(glob)

    while i < n:
        c = glob[i]

        if c == "*":
            if i + 1 < n and glob[i + 1] == "*":
                # Double star
                if i + 2 < n and glob[i + 2] == "/":
                    # `**/` → match zero or more directory segments
                    regex_parts.append("(?:[^/]+/)*")
                    i += 3
                else:
                    # `**` at end of pattern → match everything remaining
                    regex_parts.append(".*")
                    i += 2
            else:
                # Single star → match anything except /
                regex_parts.append("[^/]*")
                i += 1
        elif c == "?":
            regex_parts.append("[^/]")
            i += 1
        elif c == "[":
            # Character class. Glob uses `[!...]` for negation; regex uses
            # `[^...]`. We also need to escape regex metacharacters that
            # are special inside a class (notably `\`) without clobbering
            # class-meaningful characters like `-`, `^` (at position 0).
            j = i + 1
            # Skip a leading `!` or `^` when hunting for the closing `]`.
            if j < n and glob[j] in "!^":
                j += 1
            # An empty class `[]` is invalid; also, a `]` immediately after
            # `[` or `[!` is treated as a literal and NOT the terminator.
            if j < n and glob[j] == "]":
                j += 1
            while j < n and glob[j] != "]":
                j += 1
            if j >= n:
                # Unterminated bracket expression.
                return None
            # Extract the inner body (between [ and ])
            inner_start = i + 1
            body = glob[inner_start:j]
            negated = False
            if body.startswith("!") or body.startswith("^"):
                negated = True
                body = body[1:]
            # Escape regex metacharacters inside the class. Inside a
            # character class, the only specials that matter are `\`, `]`
            # (handled by terminator scan above), and `^` at position 0
            # (handled by `negated`). `-` is positional but we preserve
            # author intent verbatim. Escaping `\` is enough for safety.
            body = body.replace("\\", "\\\\")
            prefix = "[^" if negated else "["
            regex_parts.append(prefix + body + "]")
            i = j + 1
        else:
            regex_parts.append(re.escape(c))
            i += 1

    return "".join(regex_parts)
