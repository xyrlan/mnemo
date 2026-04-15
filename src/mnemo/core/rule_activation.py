"""Rule activation index: parsing, building, loading, and matching.

This module is the central registry for per-project activation rules.
It is imported by:
  - The PreToolUse hook (Task 3) to block / enrich before tool calls.
  - extract/__init__.py (Task 5) to rebuild the index after extraction runs.

Design constraints:
  - No imports from mnemo.core.extract.* (would create circulars).
  - load_index / log_* NEVER raise — callers rely on this for fail-open behaviour.
  - build_index MUST call is_consumer_visible — non-negotiable gate.

Known limitations (deferred to follow-up tasks):
  - Log rotation (_rotate_if_needed) is best-effort; concurrent hooks may race
    during the rename step. A 1-level rotation is sufficient for v1.
  - parse_enforce_block / parse_activates_on_block and the companion
    _describe_*_error helpers duplicate validation logic. A single walk that
    returns (parsed, error) is cleaner but out of scope for this pass.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mnemo.core.filters import is_consumer_visible, parse_frontmatter

INDEX_VERSION = 1
INDEX_FILENAME = "rule-activation-index.json"

# System tags that should be stripped from topic_tags in the index.
_SYSTEM_TAGS: frozenset[str] = frozenset({"auto-promoted", "needs-review"})

# Valid tool names for activates_on blocks.
_VALID_ENRICH_TOOLS: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit"})

# Shared regex flags used by BOTH parse-time compile and match-time search.
# Keeping them identical guarantees a pattern that parses will never raise
# re.error at match time purely from flag divergence.
_MATCH_FLAGS = re.IGNORECASE | re.DOTALL

# Catastrophic-backtracking heuristics — substrings that are rejected as a
# cheap first pass. They catch the obvious ``.*.*`` / ``(.+)+`` shapes but
# are NOT sufficient on their own — see _pattern_is_redos_safe for the
# empirical timing probe that catches the rest.
_CATASTROPHIC_SUBSTRINGS: tuple[str, ...] = (
    ".*.*",
    "(.+)+",
    "(.*)+" ,
    "(.+)*",
    "(.*)*",
)

# ReDoS timing-probe configuration. We compile the pattern then run a single
# `re.search` on a deliberately small adversarial input and reject anything
# that exceeds the budget.
#
# Input size matters here: Python's `re` has no timeout, so the probe runs
# to completion. Known ReDoS patterns like `(a+)+b` grow exponentially in
# the length of the matched prefix — on a 2 KB input they'd take hours.
# We pick a probe length short enough that the worst offenders resolve in
# a few seconds but long enough that the exponential blow-up decisively
# exceeds the budget. 22 'a's makes `(a+)+b` take ~300 ms on modern
# hardware while benign patterns like `git commit.*Co-Authored-By` take
# microseconds — a 1000x gap on either side of the 150 ms cutoff.
#
# Trailing ``X1`` triggers BOTH non-letter-terminator failure modes
# (e.g. `[a-z]+$` backtracking against a digit) and non-word-terminator
# failure modes (e.g. `(a+)+b` backtracking against an uppercase letter).
#
# The budget is hardcoded (not a config setting) so rule authors can't
# widen it to sneak bad patterns through.
_REDOS_PROBE_INPUT = "a" * 22 + "X1"
_REDOS_BUDGET_SECONDS = 0.150


def _pattern_is_redos_safe(pattern: str) -> bool:
    """Empirical ReDoS probe: compile then time a search on adversarial input.

    Returns False if the pattern fails to compile OR the probe search exceeds
    _REDOS_BUDGET_SECONDS. The probe uses the same _MATCH_FLAGS the match
    path uses, so a pattern that passes here is guaranteed compilable at
    match time.
    """
    try:
        compiled = re.compile(pattern, _MATCH_FLAGS)
    except re.error:
        return False
    start = time.perf_counter()
    try:
        compiled.search(_REDOS_PROBE_INPUT)
    except Exception:  # noqa: BLE001 — probe is defensive
        return False
    return (time.perf_counter() - start) < _REDOS_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# Dataclasses — public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnforceHit:
    slug: str
    project: str
    reason: str


@dataclass(frozen=True)
class EnrichHit:
    slug: str
    project: str
    rule_body_preview: str


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_enforce_block(fm: dict) -> dict | None:
    """Return a normalised dict {tool, deny_patterns, deny_commands, reason} or None.

    Expects ``fm["enforce"]`` to be a dict (as produced by parse_frontmatter).
    Returns None if the block is missing, invalid, or fails validation.
    """
    block = fm.get("enforce")
    if not isinstance(block, dict) or not block:
        return None

    # --- tool ---
    tool = block.get("tool")
    if tool != "Bash":
        return None

    # --- deny_pattern → deny_patterns list ---
    raw_patterns = block.get("deny_pattern") or block.get("deny_patterns")
    if isinstance(raw_patterns, str):
        raw_patterns = [raw_patterns]
    elif not isinstance(raw_patterns, list):
        raw_patterns = []

    validated_patterns: list[str] = []
    for p in raw_patterns:
        if not isinstance(p, str):
            return None
        if len(p) > 500:
            return None
        # Catastrophic backtracking — cheap substring heuristic first pass.
        if any(bad in p for bad in _CATASTROPHIC_SUBSTRINGS):
            return None
        # Then the empirical timing probe (must compile AND not exceed
        # _REDOS_BUDGET_SECONDS on an adversarial input).
        if not _pattern_is_redos_safe(p):
            return None
        validated_patterns.append(p)

    # --- deny_command → deny_commands list ---
    raw_commands = block.get("deny_command") or block.get("deny_commands")
    if isinstance(raw_commands, str):
        raw_commands = [raw_commands]
    elif not isinstance(raw_commands, list):
        raw_commands = []

    validated_commands: list[str] = []
    for c in raw_commands:
        if not isinstance(c, str) or not c:
            return None
        if len(c) > 200:
            return None
        validated_commands.append(c)

    # Must have at least one pattern or command
    if not validated_patterns and not validated_commands:
        return None

    # --- reason (required, truncate at 300) ---
    reason = block.get("reason", "")
    if not isinstance(reason, str) or not reason:
        return None
    reason = reason[:300]

    return {
        "tool": tool,
        "deny_patterns": validated_patterns,
        "deny_commands": validated_commands,
        "reason": reason,
    }


def parse_activates_on_block(fm: dict) -> dict | None:
    """Return a normalised dict {tools: list, path_globs: list} or None.

    Expects ``fm["activates_on"]`` to be a dict (as produced by parse_frontmatter).
    Returns None if the block is missing or invalid.
    """
    block = fm.get("activates_on")
    if not isinstance(block, dict) or not block:
        return None

    # --- tools ---
    tools_raw = block.get("tools")
    if not isinstance(tools_raw, list) or not tools_raw:
        return None
    for t in tools_raw:
        if t not in _VALID_ENRICH_TOOLS:
            return None

    # --- path_globs ---
    globs_raw = block.get("path_globs")
    if not isinstance(globs_raw, list) or not globs_raw:
        return None
    for g in globs_raw:
        if not isinstance(g, str) or len(g) > 200:
            return None
        # Validate structural correctness by actually translating the glob.
        # Unterminated brackets, bad classes, etc. all surface here as None.
        if _glob_to_regex(g) is None:
            return None

    return {
        "tools": list(tools_raw),
        "path_globs": list(globs_raw),
    }


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------


def projects_for_rule(source_files: list[str]) -> list[str]:
    """From a list of source_files, return sorted unique project names.

    Expects paths like ``bots/<project-name>/...``. Paths not under bots/<name>/
    are ignored.
    """
    projects: set[str] = set()
    for sf in source_files:
        parts = Path(sf).parts
        if len(parts) >= 2 and parts[0] == "bots":
            projects.add(parts[1])
    return sorted(projects)


# ---------------------------------------------------------------------------
# Body preview helper
# ---------------------------------------------------------------------------


def _body_preview(text: str, max_chars: int = 300) -> str:
    """Extract first ~max_chars characters from text, truncating at whitespace."""
    end = text.find("\n---\n", 4)
    body = text[end + 5:].strip() if end != -1 else text.strip()
    if len(body) <= max_chars:
        return body
    # Try to truncate on a whitespace boundary
    truncated = body[:max_chars]
    last_ws = max(truncated.rfind(" "), truncated.rfind("\n"), truncated.rfind("\t"))
    if last_ws > max_chars // 2:
        return truncated[:last_ws]
    return truncated


# ---------------------------------------------------------------------------
# Atomic write (inlined to avoid circular imports with extract/inbox.py)
# ---------------------------------------------------------------------------


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically using a .tmp sibling + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


# ---------------------------------------------------------------------------
# Index lifecycle
# ---------------------------------------------------------------------------


def build_index(vault_root: Path) -> dict:
    """Walk shared/feedback/*.md, build and return the full index dict.

    NON-NEGOTIABLE: calls is_consumer_visible for every candidate file.
    Never raises on bad files — records them in ``malformed``.
    """
    enforce_by_project: dict[str, list[dict]] = {}
    enrich_by_project: dict[str, list[dict]] = {}
    malformed: list[dict] = []

    feedback_dir = vault_root / "shared" / "feedback"
    candidates = sorted(feedback_dir.glob("*.md")) if feedback_dir.is_dir() else []

    for md_path in candidates:
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError as exc:
            malformed.append({"path": str(md_path), "error": f"read error: {exc}"})
            continue

        fm = parse_frontmatter(text)

        # NON-NEGOTIABLE gate
        if not is_consumer_visible(md_path, fm, vault_root):
            continue

        # Derive slug
        slug = fm.get("slug") or fm.get("name") or md_path.stem

        # Source files
        sources_raw = fm.get("sources") or []
        if isinstance(sources_raw, str):
            sources_raw = [sources_raw]
        source_files: list[str] = [s for s in sources_raw if isinstance(s, str)]

        # Topic tags — strip system markers
        tags_raw = fm.get("tags") or []
        topic_tags_list = [t for t in tags_raw if isinstance(t, str) and t not in _SYSTEM_TAGS]

        # Body preview
        preview = _body_preview(text)

        projects = projects_for_rule(source_files)
        # If no bots/ sources, still index under empty string so rules aren't
        # silently lost — but per spec, "Files not under bots/<name>/ are
        # ignored", so such rules simply don't appear in any project bucket.
        # That is correct behaviour.

        # --- enforce block ---
        enforce_block_raw = fm.get("enforce")
        if enforce_block_raw is not None:
            parsed_enforce = parse_enforce_block(fm)
            if parsed_enforce is None:
                # Determine a useful error string
                _err = _describe_enforce_error(fm)
                malformed.append({"path": str(md_path), "error": _err})
            else:
                # Pre-normalize deny_commands so match_bash_enforce can compare
                # directly against an already-normalized incoming command
                # without re-normalizing every stored entry on every call.
                normalized_deny_commands = [
                    normalize_bash_command(c)
                    for c in parsed_enforce["deny_commands"]
                ]
                entry = {
                    "slug": slug,
                    "tool": parsed_enforce["tool"],
                    "deny_patterns": parsed_enforce["deny_patterns"],
                    "deny_commands": normalized_deny_commands,
                    "reason": parsed_enforce["reason"],
                    "source_files": source_files,
                    "source_count": len(source_files),
                }
                for proj in projects:
                    enforce_by_project.setdefault(proj, []).append(entry)

        # --- activates_on block ---
        activates_on_raw = fm.get("activates_on")
        if activates_on_raw is not None:
            parsed_enrich = parse_activates_on_block(fm)
            if parsed_enrich is None:
                _err = _describe_enrich_error(fm)
                malformed.append({"path": str(md_path), "error": _err})
            else:
                entry = {
                    "slug": slug,
                    "tools": parsed_enrich["tools"],
                    "path_globs": parsed_enrich["path_globs"],
                    "topic_tags": topic_tags_list,
                    "rule_body_preview": preview,
                    "source_files": source_files,
                    "source_count": len(source_files),
                }
                for proj in projects:
                    enrich_by_project.setdefault(proj, []).append(entry)

    return {
        "schema_version": INDEX_VERSION,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault_root": str(vault_root),
        "enforce_by_project": enforce_by_project,
        "enrich_by_project": enrich_by_project,
        "malformed": malformed,
    }


def _describe_enforce_error(fm: dict) -> str:
    """Produce a descriptive error string for a failed parse_enforce_block."""
    block = fm.get("enforce", {})
    if not isinstance(block, dict):
        return "enforce: block is not a dict"
    tool = block.get("tool")
    if tool != "Bash":
        return f"enforce: tool must be 'Bash', got {tool!r}"
    # Check patterns
    raw_patterns = block.get("deny_pattern") or block.get("deny_patterns")
    if isinstance(raw_patterns, str):
        raw_patterns = [raw_patterns]
    if isinstance(raw_patterns, list):
        for p in raw_patterns:
            if isinstance(p, str) and len(p) > 500:
                return "deny_pattern exceeds 500 chars"
            if isinstance(p, str) and any(bad in p for bad in _CATASTROPHIC_SUBSTRINGS):
                return f"deny_pattern failed catastrophic-backtracking heuristic: {p!r}"
            if isinstance(p, str):
                try:
                    re.compile(p, _MATCH_FLAGS)
                except re.error as exc:
                    return f"deny_pattern failed re.compile: {exc}"
                if not _pattern_is_redos_safe(p):
                    return f"deny_pattern timed out on adversarial input: {p!r}"
    raw_commands = block.get("deny_command") or block.get("deny_commands")
    if not raw_patterns and not raw_commands:
        return "enforce: must have at least one deny_pattern or deny_command"
    reason = block.get("reason", "")
    if not reason:
        return "enforce: reason is required"
    return "enforce: block failed validation"


def _describe_enrich_error(fm: dict) -> str:
    """Produce a descriptive error string for a failed parse_activates_on_block."""
    block = fm.get("activates_on", {})
    if not isinstance(block, dict):
        return "activates_on: block is not a dict"
    tools_raw = block.get("tools")
    if not isinstance(tools_raw, list) or not tools_raw:
        return "activates_on: tools must be a non-empty list"
    for t in tools_raw:
        if t not in _VALID_ENRICH_TOOLS:
            return f"activates_on: unknown tool {t!r} (must be Edit, Write, or MultiEdit)"
    globs_raw = block.get("path_globs")
    if not isinstance(globs_raw, list) or not globs_raw:
        return "activates_on: path_globs must be a non-empty list"
    for g in globs_raw:
        if isinstance(g, str) and _glob_to_regex(g) is None:
            return f"path_glob invalid or unterminated: {g}"
    return "activates_on: block failed validation"


def write_index(vault_root: Path, index: dict) -> None:
    """Atomically write the index to <vault>/.mnemo/rule-activation-index.json."""
    mnemo_dir = vault_root / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    target = mnemo_dir / INDEX_FILENAME
    data = json.dumps(index, indent=2, default=list).encode("utf-8")
    _atomic_write_bytes(target, data)


def load_index(vault_root: Path) -> dict | None:
    """Load the index from disk. Returns None on ANY error. Never raises."""
    try:
        target = vault_root / ".mnemo" / INDEX_FILENAME
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        if raw.get("schema_version") != INDEX_VERSION:
            return None
        return raw
    except Exception:  # noqa: BLE001 — fail-open, never propagate
        return None


# ---------------------------------------------------------------------------
# Match logic
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


def match_bash_enforce(index: dict, project: str, command: str) -> EnforceHit | None:
    """Test command against the project's enforce rules.

    Hard cap: truncates to 4 KB BEFORE any matching.
    deny_patterns use re.IGNORECASE on the RAW command.
    deny_commands use prefix matching on the NORMALIZED command.
    Returns the first matching EnforceHit or None.
    """
    # Hard cap — applied BEFORE any matching
    if len(command) > 4096:
        command = command[:4096]

    normalized = normalize_bash_command(command)

    for rule in index.get("enforce_by_project", {}).get(project, []):
        # deny_patterns run on the raw command with _MATCH_FLAGS. Because
        # parse_enforce_block compiled with the same flags and ran the ReDoS
        # probe, this search is guaranteed safe to execute. We still wrap in
        # try/except as belt-and-suspenders — any re.error here would be a
        # broken invariant, so we skip the pattern rather than crash the hook.
        for pattern in rule.get("deny_patterns", []):
            try:
                if re.search(pattern, command, _MATCH_FLAGS):
                    return EnforceHit(
                        slug=rule["slug"],
                        project=project,
                        reason=rule.get("reason", rule["slug"]),
                    )
            except re.error:
                continue

        # Try deny_commands on normalized command (prefix match). deny_commands
        # are pre-normalized at build_index time, so we can compare directly
        # without re-normalizing per match.
        for dc_normalized in rule.get("deny_commands", []):
            if normalized == dc_normalized or normalized.startswith(dc_normalized + " "):
                return EnforceHit(
                    slug=rule["slug"],
                    project=project,
                    reason=rule.get("reason", rule["slug"]),
                )

    return None


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


def match_path_enrich(
    index: dict, project: str, file_path: str, tool_name: str
) -> list[EnrichHit]:
    """Return up to 3 matching EnrichHit for *file_path* filtered by *tool_name*.

    Ordered by source_count descending, then slug ascending for determinism.
    """
    matches: list[dict] = []

    for rule in index.get("enrich_by_project", {}).get(project, []):
        if tool_name not in rule.get("tools", []):
            continue
        for glob in rule.get("path_globs", []):
            if _glob_matches(glob, file_path):
                matches.append(rule)
                break  # one glob match is enough per rule

    # Sort: source_count desc, slug asc
    matches.sort(key=lambda r: (-r.get("source_count", 0), r.get("slug", "")))

    return [
        EnrichHit(
            slug=r["slug"],
            project=project,
            rule_body_preview=r.get("rule_body_preview", ""),
        )
        for r in matches[:3]
    ]


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


def _rotate_if_needed(log_path: Path, max_bytes: int) -> None:
    """Rotate log_path to log_path.1 if it exceeds max_bytes.

    Known: concurrent hooks may race here; 1-level rotation best-effort.
    """
    try:
        if log_path.exists() and log_path.stat().st_size > max_bytes:
            rotated = log_path.with_suffix(log_path.suffix + ".1")
            os.rename(log_path, rotated)
    except OSError:
        pass


def log_denial(vault_root: Path, hit: EnforceHit, tool_input: dict) -> None:
    """Append a JSON line to <vault>/.mnemo/denial-log.jsonl. Never raises."""
    try:
        from mnemo.core.config import load_config  # lazy import
        cfg = load_config()
        max_bytes: int = cfg.get("enforcement", {}).get("log", {}).get(
            "maxBytes", 1_048_576
        )

        log_path = vault_root / ".mnemo" / "denial-log.jsonl"
        _rotate_if_needed(log_path, max_bytes)

        command = tool_input.get("command", "")
        if isinstance(command, str):
            command = command[:500]

        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "slug": hit.slug,
            "project": hit.project,
            "reason": hit.reason,
            "tool": "Bash",
            "command": command,
        }

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
    except Exception:  # noqa: BLE001 — never propagate
        pass


def log_enrichment(vault_root: Path, hits: list[EnrichHit], tool_input: dict) -> None:
    """Append a JSON line to <vault>/.mnemo/enrichment-log.jsonl. Never raises."""
    try:
        from mnemo.core.config import load_config  # lazy import
        cfg = load_config()
        max_bytes: int = cfg.get("enrichment", {}).get("log", {}).get(
            "maxBytes", 1_048_576
        )

        log_path = vault_root / ".mnemo" / "enrichment-log.jsonl"
        _rotate_if_needed(log_path, max_bytes)

        project = hits[0].project if hits else ""
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "project": project,
            "hit_slugs": [h.slug for h in hits],
            "tool_name": tool_input.get("tool_name", ""),
            "file_path": tool_input.get("file_path", ""),
        }

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
    except Exception:  # noqa: BLE001 — never propagate
        pass
