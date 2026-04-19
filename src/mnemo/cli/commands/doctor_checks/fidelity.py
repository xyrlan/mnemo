"""Zero-hit fidelity check: warn when the access log shows ontology gaps."""
from __future__ import annotations

from pathlib import Path


def _doctor_check_zero_hit(vault: Path) -> bool:
    """Warn if the access log shows a high zero-hit rate — ontology may have gaps.

    Silent when fewer than 10 total calls have been logged (small samples
    produce noise). Emits a single warning + project-level breakdown (top 3
    projects by zero-hit count) when `zero_hit_rate > _ZERO_HIT_THRESHOLD`.
    """
    from mnemo.core.mcp import access_log_summary as summary_mod

    entries = summary_mod.read_log(vault)
    summary = summary_mod.summarize(entries)
    total = summary["total_calls"]
    if total < 10:
        return True

    rate = summary["zero_hit_rate"]
    if rate <= summary_mod._ZERO_HIT_THRESHOLD:
        return True

    print(f"  \u26a0 Zero-hit calls: {rate:.1%} of {total} MCP calls returned no rules (threshold {summary_mod._ZERO_HIT_THRESHOLD:.0%})")
    offenders = sorted(
        summary["by_project"].items(),
        key=lambda kv: -int(kv[1]["zero_hit"]),
    )[:3]
    for project, bucket in offenders:
        zh = int(bucket["zero_hit"])
        calls = int(bucket["calls"])
        if zh == 0:
            continue
        print(f"       \u2192 {project}: {zh}/{calls} zero-hit")
    print("       \u2192 review the tag ontology or add rules for under-covered topics")
    return False
