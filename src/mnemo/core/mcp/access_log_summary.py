"""MCP access-log aggregator — minimal summary over JSONL telemetry.

Scope: counters per tool + zero-hit rate per project.
Intentionally does NOT compute latency percentiles, top topics, or dead-rule
detection — those are Phase 3 concerns and need more data volume first.
"""
from __future__ import annotations

import json
from pathlib import Path

_LOG_FILENAME = "mcp-access-log.jsonl"
_NULL_PROJECT_BUCKET = "(unresolved)"
_REQUIRED_FIELDS = ("tool", "result_count")


def _is_well_formed(entry: dict) -> bool:
    return all(key in entry for key in _REQUIRED_FIELDS)


def summarize(entries: list[dict]) -> dict:
    """Aggregate access-log entries into a minimal summary dict.

    Entries missing required fields (tool, result_count) are skipped.
    """
    by_tool: dict[str, int] = {}
    by_project: dict[str, dict[str, int | float]] = {}
    total = 0
    zero_hits = 0

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

    return "\n".join(lines)
