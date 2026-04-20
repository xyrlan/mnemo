"""MCP access-log aggregator — minimal summary over JSONL telemetry.

Scope: counters per tool + zero-hit rate per project.
Intentionally does NOT compute latency percentiles, top topics, or dead-rule
detection — those are Phase 3 concerns and need more data volume first.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core import pricing as _pricing

_LOG_FILENAME = "mcp-access-log.jsonl"
_NULL_PROJECT_BUCKET = "(unresolved)"
_REQUIRED_FIELDS = ("tool", "result_count")

# Warn threshold for `mnemo doctor` zero-hit check. Derived from
# docs/specs/2026-04-15-mnemo-v0.5.x-retrieval-phased.md §5.3 Question B:
# ">30% zero-hit rate indicates ontology gaps".
_ZERO_HIT_THRESHOLD = 0.30


def _is_well_formed(entry: dict) -> bool:
    return all(key in entry for key in _REQUIRED_FIELDS)


def summarize(entries: list[dict]) -> dict:
    """Aggregate access-log entries into a minimal summary dict.

    Entries missing required fields (tool, result_count) are skipped.

    Adds two new top-level keys for v0.10:
    - ``llm_cost``: aggregated input/output tokens by purpose + estimated USD.
    - ``injection_stats``: SessionStart envelope size + briefing-inclusion rate.
    """
    by_tool: dict[str, int] = {}
    by_project: dict[str, dict[str, int | float]] = {}
    total = 0
    zero_hits = 0

    # NEW
    cost_by_purpose: dict[str, dict] = {}
    cost_total_in = 0
    cost_total_out = 0
    cost_total_usd = 0.0
    unknown_models: set[str] = set()
    inj_total = 0
    inj_with_briefing = 0
    inj_total_bytes = 0

    for entry in entries:
        if not _is_well_formed(entry):
            continue
        total += 1

        tool = entry["tool"]
        by_tool[tool] = by_tool.get(tool, 0) + 1

        is_zero = int(entry["result_count"]) == 0
        if is_zero:
            zero_hits += 1

        project = entry.get("project") or _NULL_PROJECT_BUCKET
        bucket = by_project.setdefault(project, {"calls": 0, "zero_hit": 0, "zero_hit_rate": 0.0})
        bucket["calls"] = int(bucket["calls"]) + 1
        if is_zero:
            bucket["zero_hit"] = int(bucket["zero_hit"]) + 1

        if tool == "llm.call":
            usage = entry.get("usage") or {}
            in_tok = int(usage.get("input_tokens", 0))
            out_tok = int(usage.get("output_tokens", 0))
            purpose = entry.get("purpose", "(unknown)")
            model = entry.get("model", "")
            p_bucket = cost_by_purpose.setdefault(purpose, {
                "calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_usd": 0.0,
            })
            p_bucket["calls"] += 1
            p_bucket["input_tokens"] += in_tok
            p_bucket["output_tokens"] += out_tok
            cost_total_in += in_tok
            cost_total_out += out_tok
            usd = _pricing.estimate_usd(model, input_tokens=in_tok, output_tokens=out_tok)
            if usd is None:
                unknown_models.add(model)
            else:
                p_bucket["estimated_usd"] = round(p_bucket["estimated_usd"] + usd, 6)
                cost_total_usd = round(cost_total_usd + usd, 6)

        elif tool == "session_start.inject":
            inj_total += 1
            inj_total_bytes += int(entry.get("envelope_bytes", 0))
            if bool(entry.get("included_briefing", False)):
                inj_with_briefing += 1

    for bucket in by_project.values():
        calls = int(bucket["calls"])
        zh = int(bucket["zero_hit"])
        bucket["zero_hit_rate"] = round(zh / calls, 4) if calls else 0.0

    zero_hit_rate = round(zero_hits / total, 4) if total else 0.0

    return {
        "total_calls": total,
        "zero_hit_calls": zero_hits,
        "zero_hit_rate": zero_hit_rate,
        "by_tool": by_tool,
        "by_project": by_project,
        "llm_cost": {
            "total_input_tokens": cost_total_in,
            "total_output_tokens": cost_total_out,
            "estimated_usd": cost_total_usd,
            "by_purpose": cost_by_purpose,
            "unknown_models": sorted(unknown_models),
        },
        "injection_stats": {
            "total_sessions": inj_total,
            "sessions_with_briefing": inj_with_briefing,
            "total_envelope_bytes": inj_total_bytes,
        },
    }


def read_log(vault_root: Path) -> list[dict]:
    """Read all JSONL lines from current + rotated access log. Skip malformed."""
    entries: list[dict] = []
    mnemo_dir = vault_root / ".mnemo"
    for name in (_LOG_FILENAME + ".1", _LOG_FILENAME):
        path = mnemo_dir / name
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def format_human(summary: dict) -> str:
    """Render summary as a plain-text report."""
    total = summary["total_calls"]
    lines = [f"Total calls: {total}"]
    if total == 0:
        lines.append("(no entries — access log is empty)")
        return "\n".join(lines)

    zh = summary["zero_hit_calls"]
    zh_rate = summary["zero_hit_rate"]
    lines.append(f"Zero-hit calls: {zh} ({zh_rate:.1%})")

    lines.append("")
    lines.append("By tool:")
    for tool, count in sorted(summary["by_tool"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {tool}: {count}")

    lines.append("")
    lines.append("By project:")
    for project, bucket in sorted(summary["by_project"].items(), key=lambda kv: -int(kv[1]["calls"])):
        calls = bucket["calls"]
        proj_zh = bucket["zero_hit"]
        proj_rate = bucket["zero_hit_rate"]
        lines.append(f"  {project}: {calls} calls, {proj_zh} zero-hit ({proj_rate:.1%})")

    cost = summary.get("llm_cost") or {}
    if cost.get("total_input_tokens", 0) or cost.get("total_output_tokens", 0):
        lines.append("")
        lines.append("LLM cost (input/output tokens, est. USD):")
        for purpose, p in sorted(cost.get("by_purpose", {}).items()):
            lines.append(
                f"  {purpose}: {p['calls']} calls, "
                f"in={p['input_tokens']:,} out={p['output_tokens']:,} "
                f"\u2248 ${p['estimated_usd']:.4f}"
            )
        lines.append(
            f"  TOTAL: in={cost['total_input_tokens']:,} "
            f"out={cost['total_output_tokens']:,} "
            f"\u2248 ${cost['estimated_usd']:.4f}"
        )
        if cost.get("unknown_models"):
            lines.append(
                f"  (cost not estimated for unknown models: {', '.join(cost['unknown_models'])})"
            )

    inj = summary.get("injection_stats") or {}
    if inj.get("total_sessions", 0):
        lines.append("")
        lines.append("SessionStart injection:")
        lines.append(
            f"  {inj['total_sessions']} sessions, "
            f"{inj['sessions_with_briefing']} with briefing, "
            f"avg envelope \u2248 {inj['total_envelope_bytes'] // max(inj['total_sessions'], 1)} bytes"
        )

    return "\n".join(lines)
