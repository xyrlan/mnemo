"""Autopilot Tier 0 — weekly health digest.

Public API:
    generate_digest(vault_root, since_days=7) -> DigestData
    render_digest_markdown(digest, date_str) -> str
    write_digest(vault_root, digest) -> Path
    post_digest_issue(digest, _run=None) -> int | None
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from mnemo.autopilot.insights._formatters import (
    fmt_pct,
    fmt_delta_pp,
    fmt_delta,
    fmt_int,
)
from mnemo.autopilot.insights._log_readers import (
    read_mcp_access_log,
    read_reflex_log,
    read_denial_log,
    read_recall_report,
)


@dataclass
class DigestData:
    # Recall
    recall_primacy_at_5: Optional[float] = None
    recall_mrr: Optional[float] = None
    recall_p95_ms: Optional[float] = None
    recall_cases: int = 0
    recall_generated_at: Optional[str] = None

    # Reflex
    reflex_prompt_count: int = 0
    reflex_emit_count: int = 0
    reflex_emit_rate: float = 0.0
    reflex_index_missing_count: int = 0
    reflex_top_silence_reasons: list = field(default_factory=list)

    # Denials
    denial_count: int = 0
    top_denial_slug: Optional[str] = None
    top_denial_count: int = 0

    # Top emitted rules (slug, count) pairs
    top_emitted_rules: list = field(default_factory=list)

    # Date string for the digest header (YYYY-MM-DD)
    date_str: str = ""

    # Window used
    since_days: int = 7


def _since_dt(since_days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=since_days)


def generate_digest(*, vault_root: Path, since_days: int = 7) -> DigestData:
    """Aggregate telemetry into a DigestData from the last ``since_days`` days."""
    since = _since_dt(since_days)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    d = DigestData(since_days=since_days, date_str=date_str)

    # ── Recall ────────────────────────────────────────────────────────────────
    report = read_recall_report(vault_root)
    if report is not None:
        r = report.get("report") or {}
        d.recall_primacy_at_5 = r.get("primacy_rate_at_5")
        d.recall_mrr = r.get("mrr")
        d.recall_p95_ms = r.get("p95_latency_ms")
        d.recall_cases = int(r.get("cases") or 0)
        d.recall_generated_at = report.get("generated_at")

    # ── Reflex ────────────────────────────────────────────────────────────────
    reflex_entries = read_reflex_log(vault_root, since_dt=since)
    d.reflex_prompt_count = len(reflex_entries)
    d.reflex_emit_count = sum(1 for e in reflex_entries if e.get("emitted"))
    if d.reflex_prompt_count > 0:
        d.reflex_emit_rate = d.reflex_emit_count / d.reflex_prompt_count

    silence_counts: Counter = Counter()
    index_missing = 0
    for e in reflex_entries:
        reason = e.get("silence_reason")
        if reason and isinstance(reason, str):
            silence_counts[reason] += 1
            if reason == "index_missing":
                index_missing += 1
    d.reflex_index_missing_count = index_missing
    d.reflex_top_silence_reasons = silence_counts.most_common(5)

    # ── Denials ───────────────────────────────────────────────────────────────
    denial_entries = read_denial_log(vault_root, since_dt=since)
    d.denial_count = len(denial_entries)
    if denial_entries:
        slug_counts: Counter = Counter(e.get("slug", "") for e in denial_entries)
        top_slug, top_count = slug_counts.most_common(1)[0]
        d.top_denial_slug = top_slug or None
        d.top_denial_count = top_count

    # ── Top emitted rules (from mcp-access-log read_mnemo_rule calls) ─────────
    mcp_entries = read_mcp_access_log(vault_root, since_dt=since)
    rule_reads: Counter = Counter()
    for e in mcp_entries:
        if e.get("tool") == "read_mnemo_rule":
            slug = (e.get("args") or {}).get("slug") or ""
            if slug:
                rule_reads[slug] += 1
    d.top_emitted_rules = rule_reads.most_common(10)

    return d


def render_digest_markdown(digest: DigestData, date_str: str) -> str:
    """Render a DigestData to the spec markdown format."""
    lines = [
        f"# Autopilot weekly digest — {date_str}",
        "",
        "## Recall",
    ]

    if digest.recall_primacy_at_5 is not None:
        lines.append(f"- primacy@5: {fmt_pct(digest.recall_primacy_at_5 * 100)}")
        if digest.recall_mrr is not None:
            lines.append(f"- MRR: {digest.recall_mrr:.4f}")
        if digest.recall_p95_ms is not None:
            lines.append(f"- p95 latency: {digest.recall_p95_ms:.0f} ms")
    else:
        lines.append("- (no recall report available)")

    lines += [
        "",
        "## Reflex",
        f"- prompts: {fmt_int(digest.reflex_prompt_count)} (last {digest.since_days}d)",
    ]
    emit_pct = fmt_pct(digest.reflex_emit_rate * 100)
    lines.append(f"- emit-rate: {emit_pct}  (target band: 3-12%)")
    if digest.reflex_top_silence_reasons:
        reasons_str = ", ".join(
            f"{r} ({c})" for r, c in digest.reflex_top_silence_reasons[:3]
        )
        lines.append(f"- top silence reasons: {reasons_str}")
    if digest.reflex_index_missing_count > 0:
        lines.append(f"- index_missing: {digest.reflex_index_missing_count}  ⚠")

    lines += [
        "",
        "## Denials",
        f"- last {digest.since_days}d: {digest.denial_count}",
    ]
    if digest.top_denial_slug:
        lines.append(
            f"- top blocker: {digest.top_denial_slug} ({digest.top_denial_count})"
        )

    lines += [
        "",
        "## Top emitted rules (last {}d)".format(digest.since_days),
    ]
    if digest.top_emitted_rules:
        for slug, count in digest.top_emitted_rules[:10]:
            lines.append(f"- {slug} ({count})")
    else:
        lines.append("- (none)")

    lines.append("")
    return "\n".join(lines)


def write_digest(*, vault_root: Path, digest: DigestData) -> Path:
    """Write digest markdown to ``<vault>/briefings/autopilot/<date>-digest.md``."""
    date_str = digest.date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = vault_root / "briefings" / "autopilot"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{date_str}-digest.md"
    md = render_digest_markdown(digest, date_str=date_str)
    path.write_text(md, encoding="utf-8")
    return path


_ISSUE_NUMBER_RE = re.compile(r"/issues/(\d+)")


def post_digest_issue(
    *,
    digest: DigestData,
    _run: Optional[Callable] = None,
) -> Optional[int]:
    """Create a GitHub issue for the digest; return issue number or None.

    *_run* is injectable for testing; defaults to ``subprocess.run``.
    Returns None when gh is unavailable, the command fails, or the
    output doesn't contain an issue URL.
    """
    if _run is None:
        import subprocess
        _run = subprocess.run

    date_str = digest.date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = f"mnemo autopilot digest — {date_str}"
    body = render_digest_markdown(digest, date_str=date_str)
    cmd = [
        "gh", "issue", "create",
        "--title", title,
        "--body", body,
        "--label", "mnemo:digest",
    ]
    try:
        result = _run(cmd, capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return None

    if result.returncode != 0:
        return None

    output = (result.stdout or "").strip()
    m = _ISSUE_NUMBER_RE.search(output)
    if m:
        return int(m.group(1))
    return None
