"""access_log_summary aggregates llm.call entries by purpose + estimates cost."""
from __future__ import annotations

from mnemo.core.mcp import access_log_summary


def _llm_entry(purpose: str, model: str, in_tok: int, out_tok: int) -> dict:
    return {
        "tool": "llm.call",
        "purpose": purpose,
        "model": model,
        "project": "myproj",
        "agent": "myagent",
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
        "elapsed_ms": 1000.0,
        "result_count": 1,
    }


def test_summary_buckets_llm_calls_by_purpose() -> None:
    entries = [
        _llm_entry("briefing", "claude-haiku-4-5", 10_000, 1_000),
        _llm_entry("briefing", "claude-haiku-4-5", 20_000, 2_000),
        _llm_entry("consolidation:feedback", "claude-haiku-4-5", 50_000, 5_000),
    ]
    summary = access_log_summary.summarize(entries)
    cost = summary["llm_cost"]
    assert cost["by_purpose"]["briefing"]["input_tokens"] == 30_000
    assert cost["by_purpose"]["briefing"]["output_tokens"] == 3_000
    assert cost["by_purpose"]["briefing"]["calls"] == 2
    assert cost["by_purpose"]["consolidation:feedback"]["calls"] == 1
    assert cost["total_input_tokens"] == 80_000
    assert cost["total_output_tokens"] == 8_000
    assert cost["estimated_usd"] > 0


def test_summary_includes_session_start_inject() -> None:
    entries = [
        {
            "tool": "session_start.inject",
            "envelope_bytes": 1234,
            "included_briefing": True,
            "project": "myproj",
            "agent": "myagent",
            "result_count": 1,
        },
        {
            "tool": "session_start.inject",
            "envelope_bytes": 567,
            "included_briefing": False,
            "project": "myproj",
            "agent": "myagent",
            "result_count": 1,
        },
    ]
    summary = access_log_summary.summarize(entries)
    inj = summary["injection_stats"]
    assert inj["total_sessions"] == 2
    assert inj["sessions_with_briefing"] == 1
    assert inj["total_envelope_bytes"] == 1234 + 567


def test_summary_unknown_model_estimated_cost_excluded() -> None:
    """Entries with unknown models contribute tokens but not USD."""
    entries = [_llm_entry("briefing", "future-model-z", 1_000_000, 1_000_000)]
    summary = access_log_summary.summarize(entries)
    cost = summary["llm_cost"]
    assert cost["total_input_tokens"] == 1_000_000
    assert cost["estimated_usd"] == 0.0
    assert "future-model-z" in cost["unknown_models"]


def test_summary_zero_hit_excludes_llm_and_inject_entries() -> None:
    """After v0.10, zero_hit_rate reflects MCP tool calls only, not llm.call or session_start.inject."""
    entries = [
        # Two MCP entries, one is zero-hit.
        {"tool": "list_rules_by_topic", "result_count": 5, "project": "p"},
        {"tool": "list_rules_by_topic", "result_count": 0, "project": "p"},
        # LLM + inject entries should NOT dilute the MCP-only denominator.
        _llm_entry("briefing", "claude-haiku-4-5", 1000, 100),
        {
            "tool": "session_start.inject",
            "envelope_bytes": 500,
            "included_briefing": False,
            "project": "p",
            "agent": "p",
            "result_count": 1,
        },
    ]
    summary = access_log_summary.summarize(entries)
    assert summary["total_calls"] == 2
    assert summary["zero_hit_calls"] == 1
    assert summary["zero_hit_rate"] == 0.5  # 1 / 2
    # by_project reflects MCP-only denominator too.
    assert summary["by_project"]["p"]["calls"] == 2
    assert summary["by_project"]["p"]["zero_hit"] == 1
    # But by_tool still shows everything.
    assert summary["by_tool"]["list_rules_by_topic"] == 2
    assert summary["by_tool"]["llm.call"] == 1
    assert summary["by_tool"]["session_start.inject"] == 1
