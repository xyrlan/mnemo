"""Activation-index lifecycle: build, write, load, project/universal resolution.

The ``build_index`` orchestrator was decomposed in v0.9 PR G: the per-file
work (read → frontmatter → visibility gate → enforce/enrich parse → entry
assembly) lives in :func:`_build_rule_entry`, and the orchestrator itself
is a slim <30-line walker over ``shared/{feedback,user,reference}/*.md``.

``_is_universal`` was renamed to public :func:`is_universal` in the same PR;
the single in-tree consumer (``reflex/index.py``) was updated atomically.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mnemo.core.errors import load_validated_json
from mnemo.core.filters import derive_rule_slug, parse_frontmatter
from mnemo.core.rule_activation.matching import normalize_bash_command
from mnemo.core.rule_activation.parsing import parse_block
from mnemo.core.text_utils import body_preview as _body_preview


def _is_consumer_visible(md_path: Path, fm: dict, vault_root: Path) -> bool:
    """Indirect lookup for ``is_consumer_visible``.

    Tests patch ``mnemo.core.rule_activation.is_consumer_visible`` on the
    shim module (that's where the v0.8 monolith hosted the imported name,
    and test_rule_activation_index.py still targets that path). Reading
    through the shim keeps those patches effective after the v0.9 split.
    Falls back to the direct ``mnemo.core.filters`` import if the shim
    attribute is absent (e.g. during partial package init).
    """
    from mnemo.core import rule_activation as _shim  # late-bound — post __init__
    try:
        return _shim.is_consumer_visible(md_path, fm, vault_root)
    except AttributeError:
        from mnemo.core.filters import is_consumer_visible
        return is_consumer_visible(md_path, fm, vault_root)

INDEX_VERSION = 3
INDEX_FILENAME = "rule-activation-index.json"

# System tags that should be stripped from topic_tags in the index.
_SYSTEM_TAGS: frozenset[str] = frozenset({"auto-promoted", "needs-review"})


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


def is_universal(projects: list[str], threshold: int) -> bool:
    """Return True when the rule's distinct project count meets the universal threshold.

    Always False for an empty project list, regardless of threshold (a rule with
    no bots/ sources is not attributable to any project and cannot be universal).
    """
    if not projects:
        return False
    return len(projects) >= threshold


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
# Build pipeline
# ---------------------------------------------------------------------------


def _build_rule_entry(
    md_path: Path,
    vault_root: Path,
    page_type: str,
    threshold: int,
    malformed: list[dict],
) -> tuple[str, dict] | None:
    """Parse one markdown file into its index entry.

    Returns ``(slug, entry)`` on success, or ``None`` when the file is
    hidden by ``is_consumer_visible`` or fails to read. Malformed
    enforce/activates_on blocks mutate *malformed* in place — the file
    itself still produces a rule entry (with the offending block nulled),
    matching the pre-refactor behaviour.

    Uses the unified :func:`parse_block` walker so the diagnostic string
    for a malformed block is read directly from the parse result rather
    than re-walked via the deleted ``_describe_*_error`` helpers.
    """
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError as exc:
        malformed.append({"path": str(md_path), "error": f"read error: {exc}"})
        return None

    fm = parse_frontmatter(text)

    if not _is_consumer_visible(md_path, fm, vault_root):
        return None

    slug = derive_rule_slug(fm, md_path.stem)

    sources_raw = fm.get("sources") or []
    if isinstance(sources_raw, str):
        sources_raw = [sources_raw]
    source_files: list[str] = [s for s in sources_raw if isinstance(s, str)]

    tags_raw = fm.get("tags") or []
    topic_tags_list = [t for t in tags_raw if isinstance(t, str) and t not in _SYSTEM_TAGS]

    preview = _body_preview(text)
    projects = projects_for_rule(source_files)
    universal = is_universal(projects, threshold)

    # --- enforce block ---
    enforce_entry = None
    if fm.get("enforce") is not None:
        parsed_enforce, err = parse_block("enforce", fm)
        if parsed_enforce is None:
            malformed.append({"path": str(md_path), "error": err or "enforce: block failed validation"})
        else:
            normalized_deny_commands = [
                normalize_bash_command(c) for c in parsed_enforce["deny_commands"]
            ]
            enforce_entry = {
                "slug": slug,
                "tool": parsed_enforce["tool"],
                "deny_patterns": parsed_enforce["deny_patterns"],
                "deny_commands": normalized_deny_commands,
                "reason": parsed_enforce["reason"],
                "source_files": source_files,
                "source_count": len(source_files),
            }

    # --- activates_on block ---
    enrich_entry = None
    if fm.get("activates_on") is not None:
        parsed_enrich, err = parse_block("activates_on", fm)
        if parsed_enrich is None:
            malformed.append({"path": str(md_path), "error": err or "activates_on: block failed validation"})
        else:
            enrich_entry = {
                "slug": slug,
                "tools": parsed_enrich["tools"],
                "path_globs": parsed_enrich["path_globs"],
                "topic_tags": topic_tags_list,
                "rule_body_preview": preview,
                "source_files": source_files,
                "source_count": len(source_files),
            }

    entry = {
        "type": page_type,
        "name": fm.get("name", slug),
        "file_stem": md_path.stem,
        "topic_tags": topic_tags_list,
        "source_files": source_files,
        "source_count": len(source_files),
        "projects": projects,
        "universal": universal,
        "body_preview": preview,
        "enforce": enforce_entry,
        "activates_on": enrich_entry,
    }
    return slug, entry


def _aggregate_rules(rules: dict[str, dict]) -> tuple[dict[str, dict], list[str], list[str]]:
    """Build ``by_project`` + universal slug / topic lists from the rules table.

    Extracted from the legacy ``build_index`` tail so the orchestrator can
    stay under 30 lines. Returns ``(by_project, universal_slugs,
    universal_topics)`` with all derived collections pre-sorted for JSON
    stability.
    """
    by_project: dict[str, dict] = {}
    universal_slugs: list[str] = []
    universal_topics: set[str] = set()

    for slug, rule in rules.items():
        for proj in rule["projects"]:
            bucket = by_project.setdefault(proj, {"local_slugs": [], "topics": set()})
            bucket["local_slugs"].append(slug)
            for t in rule["topic_tags"]:
                bucket["topics"].add(t)
        if rule["universal"]:
            universal_slugs.append(slug)
            for t in rule["topic_tags"]:
                universal_topics.add(t)

    for bucket in by_project.values():
        bucket["topics"] = sorted(bucket["topics"])
        bucket["local_slugs"] = sorted(set(bucket["local_slugs"]))

    return by_project, sorted(set(universal_slugs)), sorted(universal_topics)


def build_index(vault_root: Path, *, universal_threshold: int | None = None) -> dict:
    """Walk shared/{feedback,user,reference}/*.md, build and return the v3 index dict.

    Orchestrator only — per-file work lives in :func:`_build_rule_entry` and
    post-walk aggregation lives in :func:`_aggregate_rules`. Never raises on
    bad files; records them in ``malformed`` via the helper.
    """
    if universal_threshold is None:
        from mnemo.core.config import load_config
        universal_threshold = int(
            load_config().get("scoping", {}).get("universalThreshold", 2)
        )
    rules: dict[str, dict] = {}
    malformed: list[dict] = []
    for page_type in ("feedback", "user", "reference"):
        type_dir = vault_root / "shared" / page_type
        if not type_dir.is_dir():
            continue
        for md_path in sorted(type_dir.glob("*.md")):
            result = _build_rule_entry(md_path, vault_root, page_type, universal_threshold, malformed)
            if result is not None:
                slug, entry = result
                rules[slug] = entry
    by_project, universal_slugs, universal_topics = _aggregate_rules(rules)
    return {
        "schema_version": INDEX_VERSION,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault_root": str(vault_root),
        "rules": rules,
        "by_project": by_project,
        "universal": {"slugs": universal_slugs, "topics": universal_topics},
        "malformed": malformed,
    }


# ---------------------------------------------------------------------------
# Index I/O
# ---------------------------------------------------------------------------


def write_index(vault_root: Path, index: dict) -> None:
    """Atomically write the index to <vault>/.mnemo/rule-activation-index.json."""
    mnemo_dir = vault_root / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    target = mnemo_dir / INDEX_FILENAME
    data = json.dumps(index, indent=2, default=list).encode("utf-8")
    _atomic_write_bytes(target, data)


def load_index(vault_root: Path) -> dict | None:
    """Load the index from disk. Returns None on any error. Never raises."""
    return load_validated_json(
        vault_root / ".mnemo" / INDEX_FILENAME,
        INDEX_VERSION,
        vault_root=vault_root,
        error_namespace="rule_activation.load_index",
    )


__all__ = [
    "INDEX_FILENAME",
    "INDEX_VERSION",
    "build_index",
    "is_universal",
    "load_index",
    "projects_for_rule",
    "write_index",
]
