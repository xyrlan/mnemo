"""End-of-session rule extractor for Autopilot Tier 3.

Analyzes git signals, denial logs, and Tier 0 proposals to surface
rule candidates that the user hasn't explicitly articulated.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set

from mnemo.autopilot.proposer._git_signals import (
    git_current_branch,
    git_log_since,
    git_modified_files,
)
from mnemo.autopilot.proposer._patterns import (
    ALWAYS_KEYWORDS,
    find_repeated_patterns,
    scan_for_keywords,
)

# Confidence weights (spec-mandated, immutable)
_W_REPEATED = 0.3   # pattern occurs 2+ times
_W_MULTI_SESSION = 0.3  # appears in ≥2 sessions
_W_DENIAL = 0.2     # a denial was logged
_W_ALWAYS_KW = 0.2  # "always" / "nunca" in prompts

# Confidence threshold for auto-writing a rule stub
_AUTO_WRITE_THRESHOLD = 0.9
_AUTO_WRITE_MIN_SESSIONS = 2

# Slug hint character normalization
_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class RuleCandidate:
    """Proposed rule surfaced by Tier 3 analysis."""

    slug_hint: str
    title: str
    description: str
    confidence: float
    sessions: List[str] = field(default_factory=list)
    source: str = "tier3.eos_extractor"


def _make_slug_hint(phrase: str) -> str:
    """Convert a verb phrase to a slug hint."""
    slug = _SLUG_CLEAN_RE.sub("-", phrase.lower()).strip("-")
    return slug[:60].rstrip("-") or "rule-candidate"


def _compute_confidence(
    *,
    pattern_count: int,
    session_count: int,
    has_denial: bool,
    has_always_keyword: bool,
) -> float:
    """Compute confidence score per spec: max 1.0."""
    score = 0.0
    if pattern_count >= 2:
        score += _W_REPEATED
    if session_count >= 2:
        score += _W_MULTI_SESSION
    if has_denial:
        score += _W_DENIAL
    if has_always_keyword:
        score += _W_ALWAYS_KW
    return min(score, 1.0)


def _load_vault_slugs(vault_root: Path) -> Set[str]:
    """Scan shared/ for frontmatter slug: fields in rule .md files."""
    slugs: Set[str] = set()
    shared = vault_root / "shared"
    if not shared.is_dir():
        return slugs
    slug_re = re.compile(r"^slug:\s*(.+)$", re.MULTILINE)
    for md in shared.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
            for m in slug_re.finditer(text):
                slugs.add(m.group(1).strip())
        except OSError:
            continue
    return slugs


def _slug_similarity(a: str, b: str) -> float:
    """Simple character-overlap similarity ratio."""
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _is_duplicate(candidate_slug: str, existing_slugs: Set[str]) -> bool:
    """Return True if candidate closely matches an existing rule slug."""
    candidate_lower = candidate_slug.lower()
    for slug in existing_slugs:
        slug_lower = slug.lower()
        # Exact prefix match (e.g. "fix-nan" matches "fix-nan-normalization")
        if candidate_lower.startswith(slug_lower) or slug_lower.startswith(candidate_lower):
            return True
        # Fuzzy similarity ≥0.8
        if _slug_similarity(candidate_lower, slug_lower) >= 0.8:
            return True
    return False


def _read_denial_log(vault_root: Path, session_id: str) -> List[dict]:
    """Read denial-log.jsonl entries for the given session_id."""
    log_path = vault_root / ".mnemo" / "denial-log.jsonl"
    if not log_path.exists():
        return []
    entries: List[dict] = []
    try:
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("session_id") == session_id:
                    entries.append(entry)
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return entries


def _read_tier0_proposals(vault_root: Path, project: str) -> List[dict]:
    """Read pending Tier 0 rule_candidate proposals for this project."""
    from mnemo.autopilot.core.proposals import list_proposals

    try:
        proposals = list_proposals(
            vault_root=vault_root,
            kind="rule_candidate",
            project=project,
            status="pending",
        )
        return [
            {"slug_hint": p.payload.get("slug_hint", ""), "source": p.source}
            for p in proposals
            if p.source.startswith("tier0.")
        ]
    except Exception:
        return []


def _write_rule_stub(vault_root: Path, candidate: RuleCandidate, session_id: str) -> None:
    """Write a minimal rule stub to shared/_inbox/ for high-confidence candidates."""
    inbox = vault_root / "shared" / "_inbox" / "reference"
    inbox.mkdir(parents=True, exist_ok=True)
    slug = candidate.slug_hint
    path = inbox / f"{slug}.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    content = f"""---
slug: {slug}
title: {candidate.title}
type: reference
source: tier3.eos_extractor
confidence: {candidate.confidence:.2f}
session: {session_id}
date: {now}
runtime: true
---

{candidate.description}

<!-- auto-proposed by mnemo autopilot tier3; requires human review before merge -->
"""
    path.write_text(content, encoding="utf-8")


def analyze_session(
    *,
    session_id: str,
    project: str,
    vault_root: Path,
    cwd: Path,
    session_start_iso: Optional[str] = None,
) -> List[RuleCandidate]:
    """Analyze a session and return rule candidates.

    Reads git signals, denial log, and Tier 0 proposals. Deduplicates against
    existing vault rules. Writes proposals via the core queue; high-confidence
    candidates (≥0.9, ≥2 sessions) also get a rule stub written to _inbox/.

    Parameters
    ----------
    session_id:
        The Claude session ID being analyzed.
    project:
        Canonical project name.
    vault_root:
        Path to the mnemo vault root.
    cwd:
        Working directory of the project (for git calls).
    session_start_iso:
        ISO-8601 timestamp of session start (used for git log --since).
        Defaults to 24h ago when absent.
    """
    from mnemo.autopilot.core.proposals import write_proposal

    if session_start_iso is None:
        from datetime import timedelta

        session_start_iso = (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Gather signals ---
    commit_messages = git_log_since(cwd, session_start_iso)
    repeated_patterns = find_repeated_patterns(commit_messages, min_count=2)
    has_always_kw = scan_for_keywords(commit_messages, list(ALWAYS_KEYWORDS))

    denial_entries = _read_denial_log(vault_root, session_id)
    has_denial = len(denial_entries) > 0

    tier0_hints = _read_tier0_proposals(vault_root, project)
    tier0_slug_hints: Set[str] = {h["slug_hint"] for h in tier0_hints if h["slug_hint"]}

    existing_slugs = _load_vault_slugs(vault_root)

    candidates: List[RuleCandidate] = []

    for pattern in repeated_patterns:
        slug_hint = _make_slug_hint(pattern)

        if _is_duplicate(slug_hint, existing_slugs):
            continue

        # Check if Tier 0 also flagged this pattern (promotes confidence)
        in_tier0 = any(slug_hint in t or t in slug_hint for t in tier0_slug_hints)
        # For session count: if Tier 0 saw it, count ≥2 sessions
        session_count = 2 if in_tier0 else 1

        confidence = _compute_confidence(
            pattern_count=commit_messages.count(pattern) + 2,  # ≥2 by definition
            session_count=session_count,
            has_denial=has_denial,
            has_always_keyword=has_always_kw,
        )

        candidate = RuleCandidate(
            slug_hint=slug_hint,
            title=pattern.title(),
            description=f"Pattern detected {len(commit_messages)} time(s) in session {session_id}: {pattern}",
            confidence=confidence,
            sessions=[session_id],
        )
        candidates.append(candidate)

        # Write to proposal queue
        try:
            write_proposal(
                vault_root=vault_root,
                kind="rule_candidate",
                source="tier3.eos_extractor",
                payload={
                    "slug_hint": slug_hint,
                    "title": candidate.title,
                    "description": candidate.description,
                    "sessions": candidate.sessions,
                    "pattern": pattern,
                },
                project=project,
                confidence=confidence,
            )
        except Exception:
            pass  # queue write failure must not block analysis

        # Auto-write rule stub for very high confidence
        if confidence >= _AUTO_WRITE_THRESHOLD and session_count >= _AUTO_WRITE_MIN_SESSIONS:
            try:
                _write_rule_stub(vault_root, candidate, session_id)
            except Exception:
                pass

    return candidates
