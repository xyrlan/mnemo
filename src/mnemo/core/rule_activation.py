"""Rule activation index: parsing, building, loading, and matching.

This module is the central registry for per-project activation rules.
It is imported by:
  - The PreToolUse hook (Task 3) to block / enrich before tool calls.
  - extract/__init__.py (Task 5) to rebuild the index after extraction runs.

Design constraints:
  - No imports from mnemo.core.extract.* (would create circulars).
  - load_index / log_* NEVER raise — callers rely on this for fail-open behaviour.
  - build_index MUST call is_consumer_visible — non-negotiable gate.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mnemo.core.filters import is_consumer_visible, parse_frontmatter

INDEX_VERSION = 1
INDEX_FILENAME = "rule-activation-index.json"

# System tags that should be stripped from topic_tags in the index.
_SYSTEM_TAGS: frozenset[str] = frozenset({"auto-promoted", "needs-review"})

# Valid tool names for activates_on blocks.
_VALID_ENRICH_TOOLS: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit"})

# Catastrophic-backtracking heuristics — substrings that are rejected.
_CATASTROPHIC_SUBSTRINGS: tuple[str, ...] = (
    ".*.*",
    "(.+)+",
    "(.*)+" ,
    "(.+)*",
    "(.*)*",
)


# ---------------------------------------------------------------------------
# Dataclasses — public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnforceRule:
    slug: str
    project: str
    tool: str                    # v1: always "Bash"
    deny_patterns: tuple[str, ...]
    deny_commands: tuple[str, ...]
    reason: str
    source_files: tuple[str, ...]


@dataclass(frozen=True)
class EnrichRule:
    slug: str
    project: str
    tools: frozenset[str]        # subset of {"Edit", "Write", "MultiEdit"}
    path_globs: tuple[str, ...]
    topic_tags: tuple[str, ...]
    rule_body_preview: str       # first ~300 chars of body
    source_files: tuple[str, ...]


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
        # Catastrophic backtracking heuristic
        if any(bad in p for bad in _CATASTROPHIC_SUBSTRINGS):
            return None
        # Must compile
        try:
            re.compile(p, re.IGNORECASE)
        except re.error:
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
        topic_tags_list = [t for t in tags_raw if t not in _SYSTEM_TAGS and isinstance(t, str)]

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
                entry = {
                    "slug": slug,
                    "tool": parsed_enforce["tool"],
                    "deny_patterns": parsed_enforce["deny_patterns"],
                    "deny_commands": parsed_enforce["deny_commands"],
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
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
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
                    re.compile(p, re.IGNORECASE)
                except re.error as exc:
                    return f"deny_pattern failed re.compile: {exc}"
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

    Steps (in order):
    1. Strip leading ``sudo`` variants.
    2. Strip leading ``env KEY=VAL ...`` assignments.
    3. Strip leading shell-inline ``KEY=VAL`` assignments.
    4. Lowercase.
    5. Collapse runs of whitespace to a single space.
    """
    # Collapse whitespace first so the regex anchors work cleanly.
    cmd = re.sub(r"\s+", " ", cmd).strip()

    # Strip leading sudo (sudo, sudo -E, sudo -u foo, etc.)
    cmd = re.sub(r"^sudo\s+(-\S+\s+)*", "", cmd, flags=re.IGNORECASE)

    # Strip leading `env KEY=VAL ...` assignments
    # "env" followed by one or more KEY=VAL tokens
    cmd = re.sub(r"^env\s+(\w+=\S*\s+)+", "", cmd, flags=re.IGNORECASE)

    # Strip leading shell-inline KEY=VAL assignments (e.g. FOO=bar git ...)
    cmd = re.sub(r"^(\w+=\S*\s+)+", "", cmd)

    # Lowercase
    cmd = cmd.lower()

    # Final whitespace collapse after lowercasing
    cmd = re.sub(r"\s+", " ", cmd).strip()

    return cmd


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
        # Try deny_patterns on raw command (re.DOTALL so .* crosses newlines)
        for pattern in rule.get("deny_patterns", []):
            try:
                if re.search(pattern, command, re.IGNORECASE | re.DOTALL):
                    return EnforceHit(
                        slug=rule["slug"],
                        project=project,
                        reason=rule.get("reason", rule["slug"]),
                    )
            except re.error:
                continue

        # Try deny_commands on normalized command (prefix match)
        for deny_cmd in rule.get("deny_commands", []):
            dc_normalized = normalize_bash_command(deny_cmd)
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

    Implemented by converting the glob to a regex:
    - ``**/`` → ``(?:[^/]+/)*``  (zero or more dir segments with trailing slash)
    - ``**`` at end → ``.*``
    - ``*`` (not ``**``) → ``[^/]*``  (anything except slash)
    - ``?`` → ``[^/]``  (any single char except slash)
    - Everything else is re.escaped.
    """
    # Normalise path separators
    path = path.replace("\\", "/")
    glob = glob.replace("\\", "/")

    regex = _glob_to_regex(glob)
    try:
        return bool(re.fullmatch(regex, path))
    except re.error:
        # Fallback: should not happen with well-formed globs
        return fnmatch.fnmatchcase(path, glob)


def _glob_to_regex(glob: str) -> str:
    """Convert a glob pattern (with ``**`` support) to a regex string."""
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
            # Character class: pass through to regex as-is up to ]
            j = i + 1
            while j < n and glob[j] != "]":
                j += 1
            regex_parts.append(re.escape(glob[i:j + 1]).replace(r"\[", "[").replace(r"\]", "]"))
            # Actually, just include the bracket expression verbatim (it's already regex-compatible)
            regex_parts[-1] = glob[i:j + 1]
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
    """Rotate log_path to log_path.1 if it exceeds max_bytes."""
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
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
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
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
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
