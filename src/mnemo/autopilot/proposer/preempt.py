"""Pre-emptive briefing cache for Autopilot Tier 3.

Predicts which rules are likely relevant when a new session opens,
writes them to ``.mnemo/preempt-cache.json``, and provides a reader
used by the SessionStart hook.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from mnemo.autopilot.proposer._git_signals import (
    git_current_branch,
    git_modified_files,
)

_CACHE_FILENAME = "preempt-cache.json"
_DEFAULT_TTL_MINUTES = 30
_MAX_PREDICTED_SLUGS = 10


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cache_path(vault_root: Path) -> Path:
    return vault_root / ".mnemo" / _CACHE_FILENAME


def _cache_valid(data: dict, cwd: Path) -> bool:
    """Return True if cache is within TTL and branch matches current HEAD."""
    try:
        predicted_at_str = data.get("predicted_at", "")
        ttl_minutes = int(data.get("ttl_minutes", _DEFAULT_TTL_MINUTES))
        predicted_at = datetime.strptime(predicted_at_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        now = datetime.now(timezone.utc)
        if (now - predicted_at) > timedelta(minutes=ttl_minutes):
            return False
    except (ValueError, TypeError):
        return False

    # Branch invalidation: if branch changed since cache was written, stale
    cached_branch = data.get("branch", "")
    if cached_branch:
        current_branch = git_current_branch(cwd)
        if current_branch and current_branch != cached_branch:
            return False

    return True


def write_preempt_cache(
    *,
    vault_root: Path,
    project: str,
    slugs: List[str],
    cwd: Optional[Path] = None,
    ttl_minutes: int = _DEFAULT_TTL_MINUTES,
) -> None:
    """Write slugs to .mnemo/preempt-cache.json."""
    branch = ""
    if cwd is not None:
        branch = git_current_branch(cwd)

    cache_dir = vault_root / ".mnemo"
    cache_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "predicted_at": _now_iso(),
        "project": project,
        "slugs": slugs[:_MAX_PREDICTED_SLUGS],
        "ttl_minutes": ttl_minutes,
        "branch": branch,
    }
    _cache_path(vault_root).write_text(json.dumps(data, indent=2, sort_keys=True))


def read_preempt_cache(*, vault_root: Path, cwd: Optional[Path] = None) -> Optional[Dict]:
    """Read preempt cache; return None if missing, stale, or invalid.

    When *cwd* is provided, branch-change invalidation is applied.
    """
    path = _cache_path(vault_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    check_cwd = cwd or Path(".")
    if not _cache_valid(data, check_cwd):
        return None
    return data


def _slugs_from_rule_index(vault_root: Path, topic_keywords: List[str]) -> List[str]:
    """Look up rule slugs matching topic keywords from the rule-activation-index."""
    try:
        from mnemo.core import rule_activation

        idx = rule_activation.load_index(vault_root)
        if idx is None or "rules" not in idx:
            return []
        matched: List[str] = []
        for slug, rule in idx["rules"].items():
            rule_topics = [t.lower() for t in rule.get("topic_tags", [])]
            rule_name = rule.get("name", slug).lower()
            for kw in topic_keywords:
                kw_lower = kw.lower()
                if any(kw_lower in t for t in rule_topics) or kw_lower in rule_name:
                    if slug not in matched:
                        matched.append(slug)
                    break
        return matched
    except Exception:
        return []


def _slugs_from_branch(branch: str, vault_root: Path) -> List[str]:
    """Extract slug hints from branch name and match against rule index."""
    if not branch:
        return []
    # Extract words from branch name (feat/fix-nan-price → ["fix", "nan", "price"])
    words = re.findall(r"[a-z]+", branch.lower())
    # Filter very short/generic words
    keywords = [w for w in words if len(w) >= 3 and w not in {"the", "and", "for", "fix", "add"}]
    if not keywords:
        return []
    return _slugs_from_rule_index(vault_root, keywords)


def _slugs_from_modified_files(files: List[str], vault_root: Path) -> List[str]:
    """Infer topic keywords from file extensions and paths; look up slugs."""
    if not files:
        return []
    # Extract meaningful words from file paths
    keywords: List[str] = []
    for f in files:
        parts = re.findall(r"[a-z]+", f.lower())
        keywords.extend(p for p in parts if len(p) >= 4)
    if not keywords:
        return []
    # Deduplicate
    seen: set[str] = set()
    unique_kws: List[str] = []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            unique_kws.append(k)
    return _slugs_from_rule_index(vault_root, unique_kws[:10])


def _slugs_from_last_briefing(vault_root: Path, project: str) -> List[str]:
    """Extract mentioned slugs/topics from the last briefing's Resume at line."""
    try:
        from mnemo.core import briefing as briefing_mod

        rec = briefing_mod.pick_latest_briefing(vault_root, project)
        if rec is None:
            return []
        # Look for "Resume at" line in briefing body
        body = rec.body
        resume_match = re.search(r"Resume\s+at[:\s]+(.+)", body, re.IGNORECASE)
        if not resume_match:
            return []
        resume_text = resume_match.group(1)
        keywords = re.findall(r"[a-z]{4,}", resume_text.lower())
        return _slugs_from_rule_index(vault_root, keywords[:10])
    except Exception:
        return []


def predict_next_action(
    *,
    vault_root: Path,
    project: str,
    cwd: Path,
) -> List[str]:
    """Predict which rule slugs are likely relevant for the next session.

    Combines signals from:
    - git status (modified files → infer topic)
    - branch name (feature/fix intent)
    - last briefing "Resume at" line

    Returns up to *_MAX_PREDICTED_SLUGS* deduplicated slugs.
    """
    try:
        branch = git_current_branch(cwd)
    except Exception:
        branch = ""
    try:
        modified_files = git_modified_files(cwd)
    except Exception:
        modified_files = []

    # Gather slugs from each signal source
    all_slugs: List[str] = []
    seen: set[str] = set()

    def _add(slugs: List[str]) -> None:
        for s in slugs:
            if s not in seen:
                seen.add(s)
                all_slugs.append(s)

    try:
        _add(_slugs_from_branch(branch, vault_root))
    except Exception:
        pass
    try:
        _add(_slugs_from_modified_files(modified_files, vault_root))
    except Exception:
        pass
    try:
        _add(_slugs_from_last_briefing(vault_root, project))
    except Exception:
        pass

    return all_slugs[:_MAX_PREDICTED_SLUGS]


def preload_mcp_cache(*, vault_root: Path, slugs: List[str]) -> None:
    """No-op in v1 — SessionStart hook reads the cache file directly."""
    pass
